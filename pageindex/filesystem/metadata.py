from __future__ import annotations

import json
import math
import re
from typing import Any

from .types import MetadataField


class MetadataQueryError(ValueError):
    pass


class MetadataQueryEngine:
    FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    OPERATORS = {"$eq", "$ne", "$in", "$gt", "$gte", "$lt", "$lte"}
    LOGICAL_OPERATORS = {"$and", "$or"}
    MAX_DEPTH = 5

    def __init__(self, store: Any):
        self.store = store

    def ensure_fields(self, metadata: dict[str, Any], source: str = "source") -> None:
        fields = []
        for name, value in metadata.items():
            name = str(name)
            if not self.FIELD_RE.match(name):
                continue
            field_type = self.infer_type(value)
            if field_type is None:
                continue
            fields.append(MetadataField(name=name, field_type=field_type, source=source))
        if fields:
            self.store.upsert_metadata_fields(fields)

    def parse_filter(self, value: str | dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = self.parse_dsl(value)
        if not isinstance(value, dict):
            raise MetadataQueryError("metadata_filter must be a JSON object")
        self.validate_filter(value)
        return value

    def parse_dsl(self, dsl: str) -> dict[str, Any]:
        try:
            parsed = json.loads(dsl)
        except json.JSONDecodeError as exc:
            raise MetadataQueryError(
                "metadata DSL must be a JSON object, for example "
                '\'{"$and":[{"repo":"redwood"},{"year":{"$gte":2024}}]}\''
            ) from exc
        if not isinstance(parsed, dict):
            raise MetadataQueryError("metadata DSL must be a JSON object")
        return parsed

    def validate_filter(self, metadata_filter: dict[str, Any], depth: int = 1) -> None:
        if depth > self.MAX_DEPTH:
            raise MetadataQueryError(f"metadata_filter nesting depth exceeds {self.MAX_DEPTH}")
        if not metadata_filter:
            return
        for key, condition in metadata_filter.items():
            if key in self.LOGICAL_OPERATORS:
                self._validate_logical(key, condition, depth)
                continue
            self.validate_field(key)
            self._validate_field_condition(key, condition)

    def _validate_logical(self, operator: str, condition: Any, depth: int) -> None:
        if not isinstance(condition, list) or not condition:
            raise MetadataQueryError(f"{operator} requires a non-empty list")
        for item in condition:
            if not isinstance(item, dict):
                raise MetadataQueryError(f"{operator} items must be metadata filter objects")
            self.validate_filter(item, depth + 1)

    def _validate_field_condition(self, field: str, condition: Any) -> None:
        if not isinstance(condition, dict) or not any(
            str(key).startswith("$") for key in condition
        ):
            self._validate_scalar(condition, context=field)
            return
        if len(condition) != 1:
            raise MetadataQueryError(
                f"Field {field} condition must contain exactly one metadata operator"
            )
        operator, expected = next(iter(condition.items()))
        if operator not in self.OPERATORS:
            raise MetadataQueryError(f"Unsupported metadata operator: {operator}")
        if operator == "$in":
            if not isinstance(expected, list):
                raise MetadataQueryError(f"{field} $in requires a list")
            for item in expected:
                self._validate_scalar(item, context=f"{field} $in")
            return
        if operator in {"$gt", "$gte", "$lt", "$lte"}:
            self._validate_range_value(expected, context=f"{field} {operator}")
            return
        self._validate_scalar(expected, context=f"{field} {operator}")

    def validate_field(self, field: str) -> None:
        if not self.FIELD_RE.match(field):
            raise MetadataQueryError(f"Invalid metadata field: {field}")
        if not self.store.metadata_field_exists(field):
            raise MetadataQueryError(f"Unknown metadata field: {field}")

    def export_schema(self) -> dict[str, Any]:
        fields = {}
        for field in self.store.list_metadata_fields():
            fields[field.name] = {
                "type": field.field_type,
                "description": field.description,
            }
        return {"fields": fields}

    @classmethod
    def infer_type(cls, value: Any) -> str | None:
        if isinstance(value, list):
            for item in value:
                inferred = cls.infer_type(item)
                if inferred is not None:
                    return inferred
            return None
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return "number"
        if value is None:
            return None
        return "string"

    @staticmethod
    def _validate_scalar(value: Any, *, context: str) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise MetadataQueryError(f"{context} must be finite")
            return
        if isinstance(value, str):
            return
        raise MetadataQueryError(f"{context} must be a string, number, or boolean")

    @staticmethod
    def _validate_range_value(value: Any, *, context: str) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise MetadataQueryError(f"{context} must be a string or number")
        if isinstance(value, float) and not math.isfinite(value):
            raise MetadataQueryError(f"{context} must be finite")
