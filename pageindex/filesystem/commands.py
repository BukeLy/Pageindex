from __future__ import annotations

import json
import shlex
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .core import PageIndexFileSystem


class PIFSCommandError(ValueError):
    pass


class PIFSCommandExecutor:
    FORBIDDEN_SUBSTRINGS = (";", "`", "$(", "&&", "||")
    FORBIDDEN_TOKENS = {"|", ">", "<", ">>", "<<"}
    ALLOWED_COMMANDS = {"ls", "tree", "find", "grep", "cat", "stat", "mkdir", "cp"}

    def __init__(self, filesystem: PageIndexFileSystem, *, json_output: bool = False):
        self.filesystem = filesystem
        self.json_output = json_output

    def execute(self, command: str) -> str:
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
