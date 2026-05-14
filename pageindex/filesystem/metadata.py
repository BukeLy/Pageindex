from __future__ import annotations

import re
from typing import Any

from .types import MetadataField


class MetadataQueryError(ValueError):
    pass


class MetadataQueryEngine:
    FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    CONDITION_RE = re.compile(
        r"""
        ^\s*
        (?P<field>[A-Za-z_][A-Za-z0-9_]*)
        \s*
        (?P<op>=|==|!=|>=|<=|>|<|CONTAINS|IN)
        \s*
        (?P<value>.+?)
        \s*$
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def __init__(self, store: Any):
        self.store = store

    def ensure_fields(self, metadata: dict[str, Any], source: str = "source") -> None:
        fields = []
        for name, value in metadata.items():
            if not self.FIELD_RE.match(str(name)):
                continue
            fields.append(
                MetadataField(
                    name=str(name),
                    field_type=self.infer_type(value),
                    source=source,
                )
            )
        if fields:
            self.store.upsert_metadata_fields(fields)

    def parse_filter(self, value: str | dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            self.validate_filter(value)
            return value
        return self.parse_dsl(value)

    def parse_dsl(self, dsl: str) -> dict[str, Any]:
        parts = re.split(r"\s+AND\s+", dsl.strip(), flags=re.IGNORECASE)
        metadata_filter: dict[str, Any] = {}
        for part in parts:
            if not part.strip():
                continue
            match = self.CONDITION_RE.match(part)
            if not match:
                raise MetadataQueryError(f"Invalid metadata DSL condition: {part}")
            field = match.group("field")
            op = match.group("op").upper()
            raw_value = match.group("value")
            self.validate_field(field)
            parsed_value = self.parse_value(raw_value)
            if op in {"=", "=="}:
                metadata_filter[field] = {"$eq": parsed_value}
            elif op == "!=":
                metadata_filter[field] = {"$ne": parsed_value}
            elif op == "CONTAINS":
                metadata_filter[field] = {"$contains": parsed_value}
            elif op == "IN":
                if not isinstance(parsed_value, list):
                    raise MetadataQueryError("IN requires a list value")
                metadata_filter[field] = {"$in": parsed_value}
            elif op in {">", ">=", "<", "<="}:
                metadata_filter[field] = {f"${op}": parsed_value}
            else:
                raise MetadataQueryError(f"Unsupported metadata operator: {op}")
        self.validate_filter(metadata_filter)
        return metadata_filter

    def validate_filter(self, metadata_filter: dict[str, Any]) -> None:
        for field, condition in metadata_filter.items():
            self.validate_field(field)
            if isinstance(condition, dict):
                for operator in condition:
                    if operator not in {
                        "$eq",
                        "$ne",
                        "$contains",
                        "$in",
                        "$>",
                        "$>=",
                        "$<",
                        "$<=",
                    }:
                        raise MetadataQueryError(f"Unsupported metadata operator: {operator}")

    def validate_field(self, field: str) -> None:
        if not self.FIELD_RE.match(field):
            raise MetadataQueryError(f"Invalid metadata field: {field}")
        if not self.store.metadata_field_exists(field):
            raise MetadataQueryError(f"Unknown metadata field: {field}")

    def export_schema(self) -> dict[str, Any]:
        return {
            "schema_id": "default",
            "fields": [
                {
                    "name": field.name,
                    "type": field.field_type,
                    "description": field.description,
                    "indexed": field.indexed,
                    "faceted": field.faceted,
                    "sortable": field.sortable,
                    "source": field.source,
                }
                for field in self.store.list_metadata_fields()
            ],
        }

    @staticmethod
    def infer_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, list):
            return "array"
        return "string"

    @staticmethod
    def parse_value(raw: str) -> Any:
        value = raw.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [MetadataQueryEngine.parse_value(item) for item in inner.split(",")]
        if (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            return value[1:-1]
        lower = value.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            return value
