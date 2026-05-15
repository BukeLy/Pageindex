from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .core import PageIndexFileSystem


class PIFSCommandError(ValueError):
    pass


class PIFSCommandExecutor:
    FORBIDDEN_SUBSTRINGS = (";", "`", "$(", "||", "\n", "\r")
    FORBIDDEN_TOKENS = {"|", ">", "<", ">>", "<<", "&"}
    ALLOWED_COMMANDS = {"ls", "tree", "find", "grep", "cat", "stat", "mkdir", "cp"}
    ALLOWED_PIPE_FILTERS = {"head", "tail", "grep", "sed"}

    def __init__(self, filesystem: PageIndexFileSystem, *, json_output: bool = False):
        self.filesystem = filesystem
        self.json_output = json_output

    def execute(self, command: str) -> str:
        if not command.strip():
            raise PIFSCommandError("Empty command")
        commands = self._split_chained_commands(command)
        if len(commands) > 1:
            return "\n".join(self._execute_pipeline(part) for part in commands)
        return self._execute_pipeline(commands[0])

    def _execute_pipeline(self, command: str) -> str:
        commands = self._split_piped_commands(command)
        output = self._execute_single(commands[0])
        for pipe_command in commands[1:]:
            output = self._execute_pipe_filter(output, pipe_command)
        return output

    def _execute_single(self, command: str) -> str:
        self._validate_raw_command(command)
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            raise PIFSCommandError(f"Invalid command syntax: {exc}") from exc
        if not tokens:
            raise PIFSCommandError("Empty command")
        self._validate_tokens(tokens)
        if "--json" in tokens:
            tokens = [token for token in tokens if token != "--json"]
            json_output = True
        else:
            json_output = self.json_output
        name = tokens[0]
        if name not in self.ALLOWED_COMMANDS:
            raise PIFSCommandError(f"Unsupported command: {name}")
        data = getattr(self, f"_cmd_{name}")(tokens[1:])
        return self._render(data, json_output=json_output)

    def _execute_pipe_filter(self, input_text: str, command: str) -> str:
        self._validate_raw_command(command)
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            raise PIFSCommandError(f"Invalid command syntax: {exc}") from exc
        if not tokens:
            raise PIFSCommandError("Empty pipe command")
        self._validate_tokens(tokens)
        name = tokens[0]
        if name not in self.ALLOWED_PIPE_FILTERS:
            raise PIFSCommandError(f"Unsupported pipe command: {name}")
        if name == "head":
            return self._pipe_head_tail(input_text, tokens[1:], from_tail=False)
        if name == "tail":
            return self._pipe_head_tail(input_text, tokens[1:], from_tail=True)
        if name == "grep":
            return self._pipe_grep(input_text, tokens[1:])
        if name == "sed":
            return self._pipe_sed(input_text, tokens[1:])
        raise PIFSCommandError(f"Unsupported pipe command: {name}")

    def _cmd_ls(self, args: list[str]) -> Any:
        recursive = False
        limit = 100
        path = "/"
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in {"-R", "-r", "--recursive"}:
                recursive = True
            elif arg == "--limit":
                i += 1
                limit = int(args[i])
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported ls option: {arg}")
            else:
                path = arg
            i += 1
        return self.filesystem.browse(path, recursive=recursive, limit=limit)

    def _cmd_tree(self, args: list[str]) -> Any:
        path = "/"
        limit = 1000
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--limit":
                i += 1
                limit = int(args[i])
            elif arg == "--depth":
                i += 1
                # Depth-aware formatting can be added later; recursive browse is the v1 data source.
                int(args[i])
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported tree option: {arg}")
            else:
                path = arg
            i += 1
        return self.filesystem.browse(path, recursive=True, limit=limit)

    def _cmd_find(self, args: list[str]) -> Any:
        path = "/"
        where = None
        name = None
        limit = 10
        file_type = None
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--where":
                i += 1
                where = args[i]
            elif arg == "--name":
                i += 1
                name = args[i]
            elif arg == "--limit":
                i += 1
                limit = int(args[i])
            elif arg == "-type":
                i += 1
                file_type = args[i]
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported find option: {arg}")
            else:
                path = arg
            i += 1
        if file_type and file_type not in {"f", "d"}:
            raise PIFSCommandError("find -type supports only f or d")
        if file_type == "d":
            return self.filesystem.browse(path, recursive=True, limit=limit)["folders"]
        return self.filesystem.search(
            query=name,
            scope={"folder_path": path, "recursive": True},
            metadata_filter=where,
            limit=limit,
        )

    def _cmd_grep(self, args: list[str]) -> Any:
        recursive = False
        where = None
        limit = 10
        positionals = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in {"-R", "-r", "--recursive"}:
                recursive = True
            elif arg == "--where":
                i += 1
                where = args[i]
            elif arg == "--limit":
                i += 1
                limit = int(args[i])
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported grep option: {arg}")
            else:
                positionals.append(arg)
            i += 1
        if not positionals:
            raise PIFSCommandError("grep requires a query")
        query = positionals[0]
        path = positionals[1] if len(positionals) > 1 else "/"
        if path != "/":
            try:
                return self.filesystem.find(path, query, limit=limit)
            except (KeyError, ValueError):
                pass
        return self.filesystem.search(
            query=query,
            scope={"folder_path": path, "recursive": recursive},
            metadata_filter=where,
            limit=limit,
        )

    def _cmd_cat(self, args: list[str]) -> Any:
        if not args:
            raise PIFSCommandError("cat requires a file target")
        target = None
        location = "all"
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--range":
                i += 1
                location = args[i]
            elif arg == "--all":
                location = "all"
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported cat option: {arg}")
            else:
                target = arg
            i += 1
        if not target:
            raise PIFSCommandError("cat requires a file target")
        return self.filesystem.open(target, location)

    def _cmd_stat(self, args: list[str]) -> Any:
        if args and args[0] == "--schema":
            return self.filesystem._metadata_schema()
        if not args:
            raise PIFSCommandError("stat requires a file target or --schema")
        return self.filesystem._stat(args[0])

    def _cmd_mkdir(self, args: list[str]) -> Any:
        if len(args) != 1:
            raise PIFSCommandError("mkdir requires exactly one path")
        folder_id = self.filesystem._create_folder(args[0])
        return {"folder_id": folder_id, "path": args[0]}

    def _cmd_cp(self, args: list[str]) -> Any:
        metadata: dict[str, Any] = {}
        title = None
        external_id = None
        source_path = None
        content = None
        positionals = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--metadata-json":
                i += 1
                metadata = json.loads(args[i])
            elif arg == "--title":
                i += 1
                title = args[i]
            elif arg == "--external-id":
                i += 1
                external_id = args[i]
            elif arg == "--source-path":
                i += 1
                source_path = args[i]
            elif arg == "--content":
                i += 1
                content = args[i]
            elif arg == "--content-path":
                i += 1
                content = Path(args[i]).read_text(encoding="utf-8")
            elif arg == "--metadata-auto":
                # Reserved for future LLM-assisted metadata extraction.
                pass
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported cp option: {arg}")
            else:
                positionals.append(arg)
            i += 1
        if len(positionals) != 2:
            raise PIFSCommandError("cp requires <source_uri> <folder_path>")
        source_uri, folder_path = positionals
        content = self._read_source_content(source_uri) if content is None else content
        source_path = source_path or self._source_path_from_uri(source_uri)
        file_ref = self.filesystem.register_file(
            storage_uri=source_uri,
            source_path=source_path,
            folder_path=folder_path,
            metadata=metadata,
            external_id=external_id,
            title=title,
            content=content,
        )
        return self.filesystem._stat(file_ref)

    def _render(self, data: Any, *, json_output: bool) -> str:
        jsonable = self._jsonable(data)
        if json_output:
            return json.dumps({"ok": True, "data": jsonable}, ensure_ascii=False)
        if isinstance(jsonable, dict):
            return json.dumps(jsonable, ensure_ascii=False, indent=2)
        if isinstance(jsonable, list):
            return "\n".join(json.dumps(item, ensure_ascii=False) for item in jsonable)
        return str(jsonable)

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, list):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._jsonable(item) for key, item in value.items()}
        return value

    @classmethod
    def _validate_raw_command(cls, command: str) -> None:
        if any(token in command for token in cls.FORBIDDEN_SUBSTRINGS):
            raise PIFSCommandError("Only PageIndex FileSystem commands are allowed")

    @classmethod
    def _validate_tokens(cls, tokens: list[str]) -> None:
        if any(token in cls.FORBIDDEN_TOKENS for token in tokens):
            raise PIFSCommandError("Only PageIndex FileSystem commands are allowed")

    @classmethod
    def _split_chained_commands(cls, command: str) -> list[str]:
        return cls._split_unquoted_operator(command, "&&", reject_single_amp=True)

    @classmethod
    def _split_piped_commands(cls, command: str) -> list[str]:
        return cls._split_unquoted_operator(command, "|")

    @classmethod
    def _split_unquoted_operator(
        cls,
        command: str,
        operator: str,
        *,
        reject_single_amp: bool = False,
    ) -> list[str]:
        cls._validate_raw_command(command)
        parts: list[str] = []
        current: list[str] = []
        quote: str | None = None
        escaped = False
        i = 0
        while i < len(command):
            char = command[i]
            if escaped:
                current.append(char)
                escaped = False
                i += 1
                continue
            if char == "\\" and quote != "'":
                current.append(char)
                escaped = True
                i += 1
                continue
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
                i += 1
                continue
            if char in {"'", '"'}:
                quote = char
                current.append(char)
                i += 1
                continue
            if command.startswith(operator, i):
                part = "".join(current).strip()
                if not part:
                    raise PIFSCommandError("Invalid command syntax")
                parts.append(part)
                current = []
                i += len(operator)
                continue
            if reject_single_amp and char == "&":
                raise PIFSCommandError("Only PageIndex FileSystem commands are allowed")
            current.append(char)
            i += 1
        part = "".join(current).strip()
        if quote:
            raise PIFSCommandError("Invalid command syntax: No closing quotation")
        if not part:
            raise PIFSCommandError("Invalid command syntax")
        parts.append(part)
        return parts

    def _pipe_head_tail(self, input_text: str, args: list[str], *, from_tail: bool) -> str:
        count = self._parse_head_tail_count(args)
        payload = self._try_json_loads(input_text)
        if payload is not None:
            return self._render_json_payload(self._slice_payload(payload, count, from_tail=from_tail))
        lines = input_text.splitlines()
        selected = [] if count == 0 else lines[-count:] if from_tail else lines[:count]
        return "\n".join(selected)

    def _pipe_grep(self, input_text: str, args: list[str]) -> str:
        ignore_case = False
        invert = False
        regex = False
        patterns: list[str] = []
        for arg in args:
            if arg in {"-i", "--ignore-case"}:
                ignore_case = True
            elif arg in {"-v", "--invert-match"}:
                invert = True
            elif arg in {"-E", "--extended-regexp"}:
                regex = True
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported pipe grep option: {arg}")
            else:
                patterns.append(arg)
        if len(patterns) != 1:
            raise PIFSCommandError("pipe grep requires exactly one pattern")
        pattern = patterns[0]
        payload = self._try_json_loads(input_text)
        if payload is not None:
            return self._render_json_payload(
                self._filter_payload(
                    payload,
                    pattern,
                    ignore_case=ignore_case,
                    invert=invert,
                    regex=regex,
                )
            )
        filtered = [
            line
            for line in input_text.splitlines()
            if self._text_matches(line, pattern, ignore_case=ignore_case, invert=invert, regex=regex)
        ]
        return "\n".join(filtered)

    def _pipe_sed(self, input_text: str, args: list[str]) -> str:
        if not args:
            raise PIFSCommandError("pipe sed requires an expression")
        if args[0] == "-n":
            args = args[1:]
        if len(args) != 1:
            raise PIFSCommandError("pipe sed supports only -n '<start>,<end>p'")
        match = re.fullmatch(r"(\d+)(?:,(\d+))?p", args[0])
        if not match:
            raise PIFSCommandError("pipe sed supports only -n '<start>,<end>p'")
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        if start < 1 or end < start:
            raise PIFSCommandError("Invalid sed line range")
        payload = self._try_json_loads(input_text)
        if payload is not None:
            return self._render_json_payload(self._slice_text_payload(payload, start, end))
        lines = input_text.splitlines()
        return "\n".join(lines[start - 1 : end])

    @staticmethod
    def _parse_head_tail_count(args: list[str]) -> int:
        count = 10
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "-n":
                i += 1
                if i >= len(args):
                    raise PIFSCommandError("head/tail -n requires a count")
                count = PIFSCommandExecutor._parse_non_negative_int(args[i], "head/tail count")
            elif re.fullmatch(r"-\d+", arg):
                count = PIFSCommandExecutor._parse_non_negative_int(arg[1:], "head/tail count")
            elif arg.startswith("-"):
                raise PIFSCommandError(f"Unsupported head/tail option: {arg}")
            else:
                count = PIFSCommandExecutor._parse_non_negative_int(arg, "head/tail count")
            i += 1
        return count

    @staticmethod
    def _parse_non_negative_int(value: str, label: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise PIFSCommandError(f"{label} must be an integer") from exc
        if parsed < 0:
            raise PIFSCommandError(f"{label} must be non-negative")
        return parsed

    @staticmethod
    def _try_json_loads(input_text: str) -> Any | None:
        try:
            return json.loads(input_text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _render_json_payload(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _slice_payload(cls, payload: Any, count: int, *, from_tail: bool) -> Any:
        if isinstance(payload, list):
            return payload[-count:] if from_tail and count else payload[:count]
        if not isinstance(payload, dict):
            return payload
        sliced = dict(payload)
        if "data" in sliced:
            sliced["data"] = cls._slice_data(sliced["data"], count, from_tail=from_tail)
        else:
            sliced = cls._slice_mapping_lists(sliced, count, from_tail=from_tail)
        return sliced

    @classmethod
    def _slice_data(cls, data: Any, count: int, *, from_tail: bool) -> Any:
        if isinstance(data, list):
            return data[-count:] if from_tail and count else data[:count]
        if isinstance(data, dict):
            if isinstance(data.get("text"), str):
                copied = dict(data)
                lines = copied["text"].splitlines()
                copied["text"] = "\n".join(lines[-count:] if from_tail and count else lines[:count])
                return copied
            return cls._slice_mapping_lists(data, count, from_tail=from_tail)
        return data

    @classmethod
    def _slice_mapping_lists(cls, data: dict[str, Any], count: int, *, from_tail: bool) -> dict[str, Any]:
        copied = dict(data)
        for key, value in copied.items():
            if isinstance(value, list):
                copied[key] = value[-count:] if from_tail and count else value[:count]
        return copied

    @classmethod
    def _filter_payload(
        cls,
        payload: Any,
        pattern: str,
        *,
        ignore_case: bool,
        invert: bool,
        regex: bool,
    ) -> Any:
        if isinstance(payload, list):
            return [
                item
                for item in payload
                if cls._json_matches(item, pattern, ignore_case=ignore_case, invert=invert, regex=regex)
            ]
        if not isinstance(payload, dict):
            return payload
        filtered = dict(payload)
        if "data" in filtered:
            filtered["data"] = cls._filter_data(
                filtered["data"],
                pattern,
                ignore_case=ignore_case,
                invert=invert,
                regex=regex,
            )
        else:
            filtered = cls._filter_mapping_lists(
                filtered,
                pattern,
                ignore_case=ignore_case,
                invert=invert,
                regex=regex,
            )
        return filtered

    @classmethod
    def _filter_data(
        cls,
        data: Any,
        pattern: str,
        *,
        ignore_case: bool,
        invert: bool,
        regex: bool,
    ) -> Any:
        if isinstance(data, list):
            return [
                item
                for item in data
                if cls._json_matches(item, pattern, ignore_case=ignore_case, invert=invert, regex=regex)
            ]
        if isinstance(data, dict):
            return cls._filter_mapping_lists(
                data,
                pattern,
                ignore_case=ignore_case,
                invert=invert,
                regex=regex,
            )
        if isinstance(data, str):
            return "\n".join(
                line
                for line in data.splitlines()
                if cls._text_matches(line, pattern, ignore_case=ignore_case, invert=invert, regex=regex)
            )
        return data

    @classmethod
    def _filter_mapping_lists(
        cls,
        data: dict[str, Any],
        pattern: str,
        *,
        ignore_case: bool,
        invert: bool,
        regex: bool,
    ) -> dict[str, Any]:
        filtered = dict(data)
        for key, value in filtered.items():
            if isinstance(value, list):
                filtered[key] = [
                    item
                    for item in value
                    if cls._json_matches(item, pattern, ignore_case=ignore_case, invert=invert, regex=regex)
                ]
        return filtered

    @classmethod
    def _json_matches(
        cls,
        value: Any,
        pattern: str,
        *,
        ignore_case: bool,
        invert: bool,
        regex: bool,
    ) -> bool:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return cls._text_matches(text, pattern, ignore_case=ignore_case, invert=invert, regex=regex)

    @staticmethod
    def _text_matches(
        text: str,
        pattern: str,
        *,
        ignore_case: bool,
        invert: bool,
        regex: bool,
    ) -> bool:
        flags = re.IGNORECASE if ignore_case else 0
        if regex:
            try:
                matched = re.search(pattern, text, flags) is not None
            except re.error as exc:
                raise PIFSCommandError(f"Invalid grep regex: {exc}") from exc
        elif ignore_case:
            matched = pattern.lower() in text.lower()
        else:
            matched = pattern in text
        return not matched if invert else matched

    @classmethod
    def _slice_text_payload(cls, payload: Any, start: int, end: int) -> Any:
        if not isinstance(payload, dict):
            return payload
        sliced = dict(payload)
        data = sliced.get("data")
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            copied_data = dict(data)
            lines = copied_data["text"].splitlines()
            copied_data["text"] = "\n".join(lines[start - 1 : end])
            copied_data["start_line"] = start
            copied_data["end_line"] = min(end, len(lines))
            sliced["data"] = copied_data
        return sliced

    @staticmethod
    def _read_source_content(source_uri: str) -> str:
        parsed = urlparse(source_uri)
        if parsed.scheme == "file":
            return Path(parsed.path).read_text(encoding="utf-8")
        path = Path(source_uri)
        if parsed.scheme == "" and path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    @staticmethod
    def _source_path_from_uri(source_uri: str) -> str:
        parsed = urlparse(source_uri)
        if parsed.scheme == "file":
            return Path(parsed.path).name
        if parsed.scheme:
            return parsed.path.strip("/") or parsed.netloc
        return Path(source_uri).name
