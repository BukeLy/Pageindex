from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import time
from typing import Any, TextIO

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

STREAM_MODE_ALIASES = {
    "": "off",
    "none": "off",
    "false": "off",
    "0": "off",
    "off": "off",
    "tool": "tools",
    "tools": "tools",
    "model": "model",
    "output": "model",
    "outputs": "model",
    "think": "model",
    "all": "all",
    "debug": "all",
}
AGENT_STREAM_MODE_CHOICES = sorted(item for item in STREAM_MODE_ALIASES if item)


def normalize_agent_stream_mode(stream_mode: str | None) -> str:
    mode = STREAM_MODE_ALIASES.get((stream_mode or "off").strip().lower())
    if mode is None:
        allowed = ", ".join(sorted({"off", "tools", "model", "all"}))
        raise ValueError(f"Unknown PIFS agent stream mode: {stream_mode!r}. Allowed: {allowed}")
    return mode


class PIFSAgentStreamObserver:
    def __init__(
        self,
        stream_mode: str,
        *,
        stream_log: list[dict[str, Any]] | None = None,
        output: TextIO | None = None,
    ) -> None:
        self.stream_mode = normalize_agent_stream_mode(stream_mode)
        self.stream_log = stream_log
        self.output = output or sys.stdout
        self._printed_section: str | None = None
        self._buffers: dict[str, list[str]] = {
            "output": [],
            "think": [],
            "think_summary": [],
            "tool_args": [],
        }

    @property
    def wants_model_stream(self) -> bool:
        return self.stream_mode in {"model", "all"}

    @property
    def wants_tool_stream(self) -> bool:
        return self.stream_mode in {"tools", "all"}

    @property
    def has_output_text(self) -> bool:
        return bool(self._buffers["output"])

    def handle_event(self, event: Any) -> None:
        if getattr(event, "type", None) == "raw_response_event":
            self._handle_raw_response_event(getattr(event, "data", None))
        elif getattr(event, "type", None) == "run_item_stream_event":
            self._handle_run_item_event(event)

    def finish(self, final_output: Any = None) -> None:
        if self.wants_model_stream and not self.has_output_text and final_output:
            self._emit("output", str(final_output), "[pifs output]")
        if self._printed_section is not None:
            print(file=self.output, flush=True)
            self._printed_section = None
        if self.stream_log is not None:
            for kind, parts in self._buffers.items():
                text = "".join(parts)
                if text:
                    self.stream_log.append({"kind": kind, "text": text})

    def _handle_raw_response_event(self, data: Any) -> None:
        event_type = getattr(data, "type", "")
        delta = getattr(data, "delta", None)
        if not isinstance(delta, str) or not delta:
            return
        if event_type == "response.output_text.delta":
            self._emit("output", delta, "[pifs output]")
        elif event_type == "response.reasoning_text.delta":
            self._emit("think", delta, "[pifs think]")
        elif event_type == "response.reasoning_summary_text.delta":
            self._emit("think_summary", delta, "[pifs think summary]")
        elif event_type == "response.function_call_arguments.delta":
            self._emit("tool_args", delta, "[pifs tool args]")

    def _handle_run_item_event(self, event: Any) -> None:
        name = getattr(event, "name", "")
        item = getattr(event, "item", None)
        item_type = getattr(item, "type", "")
        if self.stream_log is not None and name in {"message_output_created", "reasoning_item_created"}:
            self.stream_log.append({"kind": "run_item", "name": name, "item_type": item_type})

    def _emit(self, kind: str, text: str, label: str) -> None:
        if kind == "tool_args":
            should_print = self.wants_tool_stream
        else:
            should_print = self.wants_model_stream
        if not should_print:
            return
        self._buffers[kind].append(text)
        if self._printed_section != kind:
            if self._printed_section is not None:
                print(file=self.output, flush=True)
            print(f"\n{label}", file=self.output, flush=True)
            self._printed_section = kind
        print(text, end="", file=self.output, flush=True)


def run_pifs_agent(
    filesystem: PageIndexFileSystem,
    question: str,
    *,
    model: str,
    root: str = "/",
    max_turns: int = 20,
    verbose: bool = False,
    stream_mode: str = "off",
    tool_log: list[dict[str, Any]] | None = None,
    agent_log: list[dict[str, Any]] | None = None,
) -> str:
    try:
        from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled
        from openai import AsyncOpenAI
    except ModuleNotFoundError as exc:
        if exc.name == "agents":
            raise RuntimeError("openai-agents is required to run the PageIndex FileSystem agent") from exc
        raise

    set_tracing_disabled(True)
    normalized_stream_mode = normalize_agent_stream_mode(stream_mode)
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
        started = time.time()
        ok = True
        try:
            output = executor.execute(command)
        except PIFSCommandError as exc:
            ok = False
            output = f"ERROR: {exc}"
        if tool_log is not None:
            tool_log.append(
                {
                    "command": command,
                    "ok": ok,
                    "seconds": round(time.time() - started, 4),
                    "output_chars": len(output),
                    "preview": output[:500],
                }
            )
        if verbose or normalized_stream_mode in {"tools", "all"}:
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
        stream_log = agent_log if normalized_stream_mode != "off" else None
        observer = PIFSAgentStreamObserver(normalized_stream_mode, stream_log=stream_log)
        streamed_run = Runner.run_streamed(agent, question, max_turns=max_turns)
        final_output = ""
        try:
            async for event in streamed_run.stream_events():
                observer.handle_event(event)
            final_output = "" if not streamed_run.final_output else str(streamed_run.final_output)
            return final_output
        finally:
            if not final_output and streamed_run.final_output:
                final_output = str(streamed_run.final_output)
            observer.finish(final_output)

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()
    except RuntimeError:
        return asyncio.run(_run())
