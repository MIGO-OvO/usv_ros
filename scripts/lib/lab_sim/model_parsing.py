from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

JsonValue = object
JsonObject = Mapping


class ModelParseError(Exception):
    __slots__ = ("code", "path", "detail")

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(code, path, detail)

    def __str__(self) -> str:
        return "{} at {}: {}".format(self.code, self.path, self.detail)


def mapping_value(raw: JsonValue | JsonObject, path: str) -> JsonObject:
    if not isinstance(raw, Mapping):
        raise ModelParseError("expected_object", path, type(raw).__name__)
    return raw


def sequence_value(raw: JsonValue, path: str) -> Sequence[JsonValue]:
    if not isinstance(raw, list):
        raise ModelParseError("expected_array", path, type(raw).__name__)
    return raw


def string_value(raw: JsonValue, path: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ModelParseError("expected_string", path, repr(raw))
    return raw


def number_value(raw: JsonValue, path: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ModelParseError("expected_number", path, repr(raw))
    value = float(raw)
    if not math.isfinite(value):
        raise ModelParseError("non_finite", path, repr(raw))
    return value


def integer_value(raw: JsonValue, path: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ModelParseError("expected_integer", path, repr(raw))
    return raw


def optional_integer_value(raw: JsonValue, path: str) -> int | None:
    return None if raw is None else integer_value(raw, path)


def boolean_value(raw: JsonValue, path: str) -> bool:
    if not isinstance(raw, bool):
        raise ModelParseError("expected_boolean", path, repr(raw))
    return raw


def schema_version_value(raw: JsonValue, path: str) -> int:
    value = integer_value(raw, path)
    if value != 2:
        raise ModelParseError("unsupported_schema", path, str(value))
    return value
