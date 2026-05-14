from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os

from .commands import PIFSCommandError, PIFSCommandExecutor
from .core import PageIndexFileSystem


AGENT_SYSTEM_PROMPT = """
You are a PageIndex FileSystem retrieval agent.

You can only inspect the corpus by calling the bash tool. The bash tool is a
PageIndex virtual shell, not a real operating-system shell.

Allowed commands:
- ls <path>
- ls -R <path>
- tree <path>
- find <path> --where '<metadata JSON DSL>' --name '<pattern>'
- grep -R '<query>' <path>
- cat <doc|ref|path> --range <start-end>
- cat <doc|ref|path> --all
- stat <doc|ref|path>
- stat --schema <path>

Metadata filters use JSON DSL, for example:
{"$and":[{"repo":"redwood"},{"year":{"$gte":2024}}]}.

Start by inspecting the relevant source folder. For retrieval questions, run at
least one grep using the full natural-language question or the longest
distinctive phrase before falling back to broad keyword searches. Do not drop
disambiguating words such as metric names, limits, dates, product names, or
error names. Use stat/cat to verify evidence. Answer only from tool output and
preserve document_ids from external_id values when present.
"""


def run_pifs_agent(
    filesystem: PageIndexFileSystem,
    question: str,
    *,
    model: str,
    root: str = "/",
    max_turns: int = 20,
    verbose: bool = False,
) -> str:
    try:
        from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled
        from openai import AsyncOpenAI
    except ModuleNotFoundError as exc:
        if exc.name == "agents":
            raise RuntimeError("openai-agents is required to run the PageIndex FileSystem agent") from exc
        raise

    set_tracing_disabled(True)
    executor = PIFSCommandExecutor(filesystem, json_output=True)
    schema = filesystem._metadata_schema()
    schema_fields = schema.get("fields", {})
    schema_sample = dict(list(schema_fields.items())[:50])
    initial_context = "\n".join(
        [
            f"Root path: {root}",
            "Top-level listing:",
            executor.execute(f"ls {root}"),
            "Metadata schema summary:",
            json.dumps(
                {
                    "field_count": len(schema_fields),
                    "sample_fields": schema_sample,
                },
                ensure_ascii=False,
            ),
        ]
    )

    @function_tool
    def bash(command: str) -> str:
        """Run one allowed PageIndex FileSystem shell command."""
        try:
            output = executor.execute(command)
        except PIFSCommandError as exc:
            output = f"ERROR: {exc}"
        if verbose:
            print(f"\n[pifs bash] {command}\n{output[:1000]}", flush=True)
        return output

    model_config = model
    if os.environ.get("OPENAI_BASE_URL"):
        model_config = OpenAIChatCompletionsModel(
            model=model,
            openai_client=AsyncOpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=os.environ.get("OPENAI_BASE_URL"),
            ),
        )

    agent = Agent(
        name="PageIndexFileSystem",
        instructions=AGENT_SYSTEM_PROMPT + "\n\n" + initial_context,
        tools=[bash],
        model=model_config,
    )

    async def _run() -> str:
        streamed_run = Runner.run_streamed(agent, question, max_turns=max_turns)
        async for _event in streamed_run.stream_events():
            pass
        return "" if not streamed_run.final_output else str(streamed_run.final_output)

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()
    except RuntimeError:
        return asyncio.run(_run())
