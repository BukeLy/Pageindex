from __future__ import annotations

import asyncio
import concurrent.futures

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
- find <path> --where '<metadata DSL>' --name '<pattern>'
- grep -R '<query>' <path>
- cat <doc|ref|path> --range <start-end>
- cat <doc|ref|path> --all
- stat <doc|ref|path>
- stat --schema <path>

Start by inspecting the top-level folders and metadata schema. Use find/grep to
narrow candidate documents, then use stat/cat to verify evidence. Answer only
from tool output and preserve document_ids from external_id values when present.
"""


def run_pifs_agent(
    filesystem: PageIndexFileSystem,
    question: str,
    *,
    model: str,
    root: str = "/",
    verbose: bool = False,
) -> str:
    try:
        from agents import Agent, Runner, function_tool, set_tracing_disabled
    except ModuleNotFoundError as exc:
        if exc.name == "agents":
            raise RuntimeError("openai-agents is required to run the PageIndex FileSystem agent") from exc
        raise

    set_tracing_disabled(True)
    executor = PIFSCommandExecutor(filesystem, json_output=True)
    initial_context = "\n".join(
        [
            f"Root path: {root}",
            "Top-level listing:",
            executor.execute(f"ls {root}"),
            "Metadata schema:",
            executor.execute("stat --schema /"),
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

    agent = Agent(
        name="PageIndexFileSystem",
        instructions=AGENT_SYSTEM_PROMPT + "\n\n" + initial_context,
        tools=[bash],
        model=model,
    )

    async def _run() -> str:
        streamed_run = Runner.run_streamed(agent, question)
        async for _event in streamed_run.stream_events():
            pass
        return "" if not streamed_run.final_output else str(streamed_run.final_output)

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()
    except RuntimeError:
        return asyncio.run(_run())
