"""Contrato do webhook: somente dados, sem executar IA, ferramentas ou áudio."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from tools.registry import ArgumentError, decode_json

BODY_LIMIT = 8192
TEXT_LIMIT = 2048
DATA_LIMIT = 4096
EXPRESSIONS = frozenset({
    "neutral", "listening", "thinking", "happy", "sad", "angry", "surprised",
    "talking", "sleepy", "confused", "worried", "wink", "love", "playful",
    "silly", "smirk", "working_hard", "excited", "cool", "cry",
})
FIELDS = frozenset({
    "schema_version", "event_id", "kind", "device_id", "producer", "occurred_at",
    "expires_in_seconds", "text", "data", "humanize", "expression", "action",
})
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)")


class EventError(ValueError):
    """Apenas código fixo sanitizado; nunca inclui dados da requisição."""

    def __init__(self, code, status=422, retry_after=None):
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(compact(value).encode("utf-8")).hexdigest()


def text_value(value, name, byte_limit, *, required=False, multiline=False):
    if type(value) is not str:
        raise EventError("event_" + name + "_invalid")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise EventError("event_" + name + "_invalid") from None
    if (size > byte_limit or required and not value.strip()
            or any((ord(char) < 32 and not (multiline and char in "\n\r\t"))
                   or ord(char) == 127 for char in value)):
        raise EventError("event_" + name + "_invalid")
    return value


def data_value(value):
    if type(value) is not dict:
        raise EventError("event_data_invalid")

    def visit(item, depth=0):
        if depth > 6:
            raise EventError("event_data_depth")
        if type(item) is dict:
            for key, child in item.items():
                text_value(key, "data_key", 128, required=True)
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            text_value(item, "data_text", DATA_LIMIT, multiline=True)
        elif type(item) is float:
            if not math.isfinite(item):
                raise EventError("event_data_invalid")
        elif item is not None and type(item) not in {int, bool}:
            raise EventError("event_data_invalid")

    visit(value)
    if len(compact(value).encode("utf-8")) > DATA_LIMIT:
        raise EventError("event_data_limit")
    return value


def timestamp_value(value):
    if type(value) is not str or not TIMESTAMP.fullmatch(value):
        raise EventError("event_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise EventError("event_timestamp_invalid") from None


@dataclass(frozen=True)
class EventSubmission:
    document: dict
    payload_json: str
    payload_hash: str
    request_hash: str


def decode_event(body):
    if len(body) > BODY_LIMIT:
        raise EventError("event_body_limit", 413)
    try:
        request = decode_json(body.decode("utf-8"))
    except (UnicodeError, ArgumentError):
        raise EventError("event_json_invalid") from None
    if (type(request) is not dict or set(request) - FIELDS
            or not {"schema_version", "event_id", "kind"} <= set(request)):
        raise EventError("event_fields_invalid")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise EventError("event_version_invalid")
    event_id = text_value(request["event_id"], "id", 512, required=True)
    if len(event_id) > 128:
        raise EventError("event_id_invalid")
    kind = request["kind"]
    if type(kind) is not str or kind not in {"notification", "reaction", "command"}:
        raise EventError("event_kind_invalid")
    return request


def request_fingerprint(request):
    canonical = dict(request)
    if canonical.get("expression") is None:
        canonical["expression"] = "neutral"
    if "occurred_at" in canonical:
        canonical["occurred_at"] = timestamp_value(canonical["occurred_at"])
    try:
        return fingerprint(canonical)
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        raise EventError("event_json_invalid") from None


def parse_event(request, config):
    # A rota já conferiu JSON, campos, versão e identidade antes de consultar dedupe.
    event_id, kind = request["event_id"], request["kind"]
    device = request.get("device_id", config.device_id)
    if type(device) is not str or device != config.device_id:
        raise EventError("event_device_unknown")
    expression = request.get("expression")
    expression = "neutral" if expression is None else expression
    if type(expression) is not str or expression not in EXPRESSIONS:
        raise EventError("event_expression_invalid")
    ttl = request.get("expires_in_seconds", config.ttl_seconds)
    if type(ttl) is not int or not 1 <= ttl <= 86400:
        raise EventError("event_ttl_invalid")
    producer = text_value(request.get("producer", ""), "producer", 512)
    if len(producer) > 128:
        raise EventError("event_producer_invalid")
    document = {"schema_version": 1, "event_id": event_id, "kind": kind,
                "device_id": device, "expression": expression,
                "expires_in_seconds": ttl, "producer": producer}
    if "occurred_at" in request:
        document["occurred_at"] = timestamp_value(request["occurred_at"])
    if kind == "notification":
        if "action" in request:
            raise EventError("event_action_not_allowed")
        humanize = request.get("humanize", config.humanize_default)
        if type(humanize) is not bool:
            raise EventError("event_humanize_invalid")
        text = text_value(request.get("text", ""), "text", TEXT_LIMIT, multiline=True)
        data = data_value(request.get("data", {}))
        if not text.strip() and (not humanize or not data):
            raise EventError("event_text_required")
        if "confirmed_state" in data and "occurred_at" not in document:
            raise EventError("event_timestamp_required")
        document.update(humanize=humanize, text=text, data=data)
    elif kind == "reaction":
        if {"text", "data", "humanize", "action"} & set(request):
            raise EventError("event_reaction_fields_invalid")
    else:
        if {"data", "humanize"} & set(request):
            raise EventError("event_command_fields_invalid")
        action = request.get("action")
        if (type(action) is not dict or set(action) != {"tool", "command"}
                or action["tool"] != "home_assistant"):
            raise EventError("event_action_invalid")
        command = text_value(action["command"], "command", TEXT_LIMIT, required=True, multiline=True)
        text = text_value(request.get("text", ""), "text", TEXT_LIMIT, multiline=True)
        document.update(text=text, action={"tool": "home_assistant", "command": command})
    # O hash do pedido preserva retries idênticos mesmo se defaults do .env mudarem.
    # O hash efetivo também equipara valores explícitos aos defaults já resolvidos.
    try:
        return EventSubmission(document, compact(document), fingerprint(document),
                               request_fingerprint(request))
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        raise EventError("event_json_invalid") from None
