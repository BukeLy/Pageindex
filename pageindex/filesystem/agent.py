from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from typing import Any, TextIO

from .commands import PIFSCommandError, PIFSCommandExecutor
from .core import PageIndexFileSystem


AGENT_SYSTEM_PROMPT = """
You are a PageIndex FileSystem retrieval agent.

You can only inspect the corpus by calling the bash tool. The bash tool is a
PageIndex virtual shell, not a real operating-system shell.

Follow the task prompt for command policy, retrieval strategy, and answer
format. If the caller needs stricter behavior, pass an explicit system_prompt.
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
REASONING_EFFORT_CHOICES = ["none", "minimal", "low", "medium", "high", "xhigh"]
REASONING_SUMMARY_CHOICES = ["none", "auto", "concise", "detailed"]


def should_use_openai_compatible_chat_model(base_url: str | None) -> bool:
    if not base_url:
        return False
    normalized = base_url.strip().rstrip("/")
    return normalized not in {"https://api.openai.com", "https://api.openai.com/v1"}


def normalize_reasoning_effort(reasoning_effort: str | None) -> str | None:
    if reasoning_effort is None or not reasoning_effort.strip():
        return None
    effort = reasoning_effort.strip().lower()
    if effort not in REASONING_EFFORT_CHOICES:
        allowed = ", ".join(REASONING_EFFORT_CHOICES)
        raise ValueError(f"Unknown reasoning effort: {reasoning_effort!r}. Allowed: {allowed}")
    return effort


def normalize_reasoning_summary(reasoning_summary: str | None) -> str | None:
    if reasoning_summary is None or not reasoning_summary.strip():
        return None
    summary = reasoning_summary.strip().lower()
    if summary not in REASONING_SUMMARY_CHOICES:
        allowed = ", ".join(REASONING_SUMMARY_CHOICES)
        raise ValueError(f"Unknown reasoning summary: {reasoning_summary!r}. Allowed: {allowed}")
    return None if summary == "none" else summary


def build_agent_model_settings(
    *,
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
) -> Any | None:
    effort = normalize_reasoning_effort(reasoning_effort)
    summary = normalize_reasoning_summary(reasoning_summary)
    if effort is None and summary is None:
        return None
    if effort not in {None, "none"} and summary is None:
        summary = "auto"

    from agents import ModelSettings
    from openai.types.shared import Reasoning

    reasoning_kwargs = {}
    if effort is not None:
        reasoning_kwargs["effort"] = effort
    if summary is not None:
        reasoning_kwargs["summary"] = summary
    return ModelSettings(reasoning=Reasoning(**reasoning_kwargs), verbosity="low")


def normalize_agent_stream_mode(stream_mode: str | None) -> str:
    mode = STREAM_MODE_ALIASES.get((stream_mode or "off").strip().lower())
    if mode is None:
        allowed = ", ".join(sorted({"off", "tools", "model", "all"}))
        raise ValueError(f"Unknown PIFS agent stream mode: {stream_mode!r}. Allowed: {allowed}")
    return mode


def serialize_agent_final_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    if is_dataclass(value):
        return json.dumps(asdict(value), ensure_ascii=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


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
            self._emit("output", str(final_output), "[llm final output stream]")
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
            self._emit("output", delta, "[llm final output stream]")
        elif event_type == "response.reasoning_text.delta":
            self._emit("think", delta, "[llm reasoning text stream]")
        elif event_type == "response.reasoning_summary_text.delta":
            self._emit("think_summary", delta, "[llm reasoning summary stream]")
        elif event_type == "response.function_call_arguments.delta":
            self._buffers["tool_args"].append(delta)

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

    def emit_tool_call(self, command: str, *, force: bool = False) -> None:
        if self.stream_log is not None:
            self.stream_log.append({"kind": "tool_call", "command": command})
        if not (force or self.wants_tool_stream):
            return
        self._start_section("tool_call", "[llm -> pifs command]")
        print(command, file=self.output, flush=True)

    def emit_tool_result(
        self,
        *,
        ok: bool,
        output: str,
        seconds: float,
        force: bool = False,
        preview_chars: int = 1000,
    ) -> None:
        if self.stream_log is not None:
            self.stream_log.append(
                {
                    "kind": "tool_result",
                    "ok": ok,
                    "seconds": round(seconds, 4),
                    "output_chars": len(output),
                    "preview": output[:preview_chars],
                }
            )
        if not (force or self.wants_tool_stream):
            return
        preview = output[:preview_chars]
        if len(output) > preview_chars:
            preview += f"\n... [truncated {len(output) - preview_chars} chars]"
        self._start_section("tool_result", "[pifs -> llm result preview]")
        print(
            f"ok={str(ok).lower()} seconds={seconds:.4f} output_chars={len(output)}",
            file=self.output,
            flush=True,
        )
        print(preview, file=self.output, flush=True)

    def _start_section(self, kind: str, label: str) -> None:
        if self._printed_section is not None:
            print(file=self.output, flush=True)
        print(f"\n{label}", file=self.output, flush=True)
        self._printed_section = kind


def run_pifs_agent(
    filesystem: PageIndexFileSystem,
    question: str,
    *,
    model: str,
    root: str = "/",
    system_prompt: str | None = None,
    max_turns: int = 20,
    max_seconds: float | None = 60,
    verbose: bool = False,
    stream_mode: str = "off",
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
    output_type: type[Any] | None = None,
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
    executor = PIFSCommandExecutor(filesystem, json_output=False)
    observer = PIFSAgentStreamObserver(normalized_stream_mode, stream_log=agent_log)
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
        """Run allowed PageIndex FileSystem shell commands, optionally chained with &&."""
        started = time.time()
        ok = True
        observer.emit_tool_call(command, force=verbose)
        try:
            output = executor.execute(command)
        except PIFSCommandError as exc:
            ok = False
            output = f"ERROR: {exc}"
        seconds = time.time() - started
        if tool_log is not None:
            tool_log.append(
                {
                    "command": command,
                    "ok": ok,
                    "seconds": round(seconds, 4),
                    "output_chars": len(output),
                    "preview": output[:500],
                }
            )
        observer.emit_tool_result(ok=ok, output=output, seconds=seconds, force=verbose)
        return output

    model_settings = build_agent_model_settings(
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
    )
    base_url = os.environ.get("OPENAI_BASE_URL")
    model_config = model
    if should_use_openai_compatible_chat_model(base_url):
        model_config = OpenAIChatCompletionsModel(
            model=model,
            openai_client=AsyncOpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=base_url,
            ),
        )

    agent_kwargs: dict[str, Any] = {
        "name": "PageIndexFileSystem",
        "instructions": (system_prompt or AGENT_SYSTEM_PROMPT).strip() + "\n\n" + initial_context,
        "tools": [bash],
        "model": model_config,
    }
    if model_settings is not None:
        agent_kwargs["model_settings"] = model_settings
    if output_type is not None:
        agent_kwargs["output_type"] = output_type
    agent = Agent(**agent_kwargs)

    async def _run_streamed() -> str:
        streamed_run = Runner.run_streamed(agent, question, max_turns=max_turns)
        final_output = ""
        try:
            async for event in streamed_run.stream_events():
                observer.handle_event(event)
            final_output = serialize_agent_final_output(streamed_run.final_output)
            return final_output
        finally:
            if not final_output and streamed_run.final_output:
                final_output = serialize_agent_final_output(streamed_run.final_output)
            observer.finish(final_output)

    async def _run() -> str:
        if max_seconds is None or max_seconds <= 0:
            return await _run_streamed()
        try:
            return await asyncio.wait_for(_run_streamed(), timeout=max_seconds)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"MaxSecondsExceeded: exceeded {max_seconds:g}s") from exc

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()
    except RuntimeError:
        return asyncio.run(_run())
