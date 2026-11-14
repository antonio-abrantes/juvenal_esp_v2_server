"""Contratos de ferramentas e validação restrita dos argumentos recebidos."""

import asyncio
import json
import math
import re
from dataclasses import dataclass, field

from config import settings


class ArgumentError(ValueError):
    pass


def decode_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ArgumentError("duplicate_json_key")
            value[key] = item
        return value

    def constant(_):
        raise ArgumentError("non_finite_json")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError):
        raise ArgumentError("invalid_json") from None


def validate_arguments(value, schema, depth=0):
    if depth > 6:
        raise ArgumentError("argument_depth")
    kind = schema.get("type")
    valid = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "boolean": type(value) is bool,
             "integer": type(value) is int,
             "number": type(value) in {int, float}}.get(kind, False)
    if not valid or "enum" in schema and value not in schema["enum"]:
        raise ArgumentError("argument_type_or_enum")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(value) - set(properties) or set(schema.get("required", [])) - set(value):
            raise ArgumentError("argument_fields")
        for key, item in value.items():
            validate_arguments(item, properties[key], depth + 1)
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 32):
            raise ArgumentError("argument_array_limit")
        for item in value:
            validate_arguments(item, schema["items"], depth + 1)
    elif kind == "string":
        if (not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 512)
                or any(ord(char) < 32 for char in value)
                or schema.get("pattern") and not re.fullmatch(schema["pattern"], value)):
            raise ArgumentError("argument_string")
    elif kind in {"integer", "number"}:
        if (type(value) is float and not math.isfinite(value) or value < schema.get("minimum", -1000000)
                or value > schema.get("maximum", 1000000)):
            raise ArgumentError("argument_range")


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict

    def message(self):
        return {"id": self.call_id, "type": "function", "function": {
            "name": self.name,
            "arguments": json.dumps(self.arguments, ensure_ascii=False, separators=(",", ":"))}}


@dataclass(frozen=True)
class ToolResult:
    status: str
    code: str
    message: str
    data: dict = field(default_factory=dict)

    def content(self):
        return json.dumps({"status": self.status, "code": self.code,
                           "message": self.message, "data": self.data}, ensure_ascii=False)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    handler: object
    permission: object
    timeout: float = 15

    def schema(self):
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self):
        self.definitions = {}

    def register(self, definition):
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", definition.name)
                or definition.name in self.definitions):
            raise ValueError("invalid_tool_registration")
        self.definitions[definition.name] = definition

    async def execute(self, call, session):
        definition = self.definitions.get(call.name)
        if definition is None or not definition.permission(session):
            return ToolResult("denied", "tool_not_allowed", "Esta ferramenta não está disponível.")
        try:
            session.check()
            validate_arguments(call.arguments, definition.parameters)
        except ArgumentError:
            return ToolResult("denied", "invalid_tool_arguments", "Os parâmetros da ação são inválidos.")
        try:
            result = await asyncio.wait_for(definition.handler(call, session),
                                            min(definition.timeout, session.remaining))
            if (not isinstance(result, ToolResult)
                    or result.status not in {"success", "denied", "needs_confirmation", "failed", "unknown"}
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", result.code)
                    or not isinstance(result.message, str)
                    or len(result.message.encode("utf-8")) > settings.text_bytes or type(result.data) is not dict
                    or len(json.dumps(result.data, allow_nan=False).encode("utf-8")) > 4096):
                return ToolResult("unknown", "tool_result_invalid",
                                  "O resultado não pôde ser validado. Não vou repetir a ação.")
            return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return ToolResult("unknown", "tool_timeout",
                              "Não foi possível confirmar o resultado. A ação não será repetida.")
        except Exception:
            return ToolResult("unknown", "tool_failed",
                              "Não foi possível confirmar o resultado da ferramenta. Não vou repetir.")
