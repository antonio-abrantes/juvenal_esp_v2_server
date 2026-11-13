"""Catálogo Markdown determinístico, com revisão otimista e troca atômica."""

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from uuid import uuid4

from config import tool_settings
from tools.registry import ArgumentError, decode_json, validate_arguments

ENTITY = {"type": "string", "minLength": 3, "maxLength": 128}
TRANSITION = {"type": "number", "minimum": 0, "maximum": 60}
ACTIONS = {
    "light.turn_on": {"domain": "light", "verb": "Ligue a luz", "parameters": {
        "entity_id": ENTITY, "brightness_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "transition": TRANSITION}},
    "light.turn_off": {"domain": "light", "verb": "Desligue a luz", "parameters": {
        "entity_id": ENTITY, "transition": TRANSITION}},
    "switch.turn_on": {"domain": "switch", "verb": "Ligue o interruptor", "parameters": {"entity_id": ENTITY}},
    "switch.turn_off": {"domain": "switch", "verb": "Desligue o interruptor", "parameters": {"entity_id": ENTITY}},
    "scene.turn_on": {"domain": "scene", "verb": "Ative a cena", "parameters": {"entity_id": ENTITY}},
    "script.turn_on": {"domain": "script", "verb": "Execute o script", "parameters": {"entity_id": ENTITY}},
    "automation.trigger": {"domain": "automation", "verb": "Execute a automação", "parameters": {"entity_id": ENTITY}},
}
PREFIX = "# Catálogo de automações do Juvenal\n\n" + chr(96) * 3 + "json\n"
SUFFIX = "\n" + chr(96) * 3 + "\n"
ENTRY_KEYS = {"id", "title", "description", "enabled", "examples", "action",
              "parameters", "confirmation", "permissions", "result_hint"}


class CatalogError(ValueError):
    def __init__(self, code, revision=""):
        super().__init__(code)
        self.code, self.revision = code, revision


def text_field(value, maximum, empty=False):
    if (type(value) is not str or len(value) > maximum or not empty and not value.strip()
            or any(ord(char) < 32 for char in value)
            or re.search(r"(?i)https?://|(?:api[_-]?key|password|token|secret)\s*[:=]", value)):
        raise CatalogError("catalog_text_invalid")
    return value.strip()


def validate_entry(entry):
    if type(entry) is not dict or set(entry) != ENTRY_KEYS:
        raise CatalogError("catalog_entry_fields")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", text_field(entry["id"], 64)):
        raise CatalogError("catalog_id_invalid")
    for key, limit in (("title", 100), ("description", 400), ("result_hint", 200)):
        text_field(entry[key], limit, empty=key != "title")
    if (type(entry["enabled"]) is not bool or type(entry["confirmation"]) is not str
            or entry["confirmation"] not in {"always", "never"}):
        raise CatalogError("catalog_policy_invalid")
    for key, limit in (("examples", 8), ("permissions", 16)):
        values = entry[key]
        if type(values) is not list or len(values) > limit:
            raise CatalogError("catalog_list_invalid")
        for item in values:
            text_field(item, 160)
        if len(set(values)) != len(values):
            raise CatalogError("catalog_duplicate_value")
    if not entry["permissions"] or any(
            not re.fullmatch(r"local|web:operator|esp:[A-Za-z0-9_-]{1,64}", item)
            for item in entry["permissions"]):
        raise CatalogError("catalog_permissions_invalid")
    action = ACTIONS.get(entry["action"]) if isinstance(entry["action"], str) else None
    if action is None or type(entry["parameters"]) is not dict:
        raise CatalogError("catalog_action_invalid")
    parameters = entry["parameters"]
    if "entity_id" not in parameters or set(parameters) - set(action["parameters"]):
        raise CatalogError("catalog_parameters_invalid")
    for name, schema in parameters.items():
        allowed = action["parameters"][name]
        if (type(schema) is not dict or schema.get("type") != allowed["type"]
                or type(schema.get("required")) is not bool
                or set(schema) - {"type", "required", "enum", "minimum", "maximum"}):
            raise CatalogError("catalog_parameter_schema")
        if allowed["type"] == "string":
            if name != "entity_id" or set(schema) != {"type", "required", "enum"}:
                raise CatalogError("catalog_entity_schema")
            values = schema["enum"]
            if (not schema["required"] or type(values) is not list or not 1 <= len(values) <= 32
                    or any(type(item) is not str or not re.fullmatch(
                        re.escape(action["domain"]) + r"\.[a-z0-9_]{1,100}", item) for item in values)
                    or len(set(values)) != len(values)):
                raise CatalogError("catalog_entity_allowlist")
        else:
            if set(schema) != {"type", "required", "minimum", "maximum"}:
                raise CatalogError("catalog_numeric_schema")
            try:
                validate_arguments(schema["minimum"], allowed)
                validate_arguments(schema["maximum"], allowed)
            except ArgumentError:
                raise CatalogError("catalog_parameter_range") from None
            if schema["minimum"] > schema["maximum"]:
                raise CatalogError("catalog_parameter_range")
    return entry


def validate_catalog(document):
    if (type(document) is not dict or set(document) != {"schema_version", "automations"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or type(document["automations"]) is not list
            or len(document["automations"]) > tool_settings.catalog_entries):
        raise CatalogError("catalog_schema_invalid")
    ids = set()
    for entry in document["automations"]:
        validate_entry(entry)
        if entry["id"] in ids:
            raise CatalogError("catalog_duplicate_id")
        ids.add(entry["id"])
    return document


def validate_parameters(entry, arguments):
    definitions = entry["parameters"]
    properties = {key: {k: v for k, v in schema.items() if k != "required"}
                  for key, schema in definitions.items()}
    validate_arguments(arguments, {"type": "object", "properties": properties,
        "required": [key for key, schema in definitions.items() if schema["required"]]})


@dataclass(frozen=True)
class CatalogSnapshot:
    revision: str
    automations: list


class CatalogStore:
    def __init__(self, data_dir):
        self.path = data_dir / "automacoes.md"
        self.lock = threading.RLock()

    def _raw(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(tool_settings.catalog_bytes + 1)
        except FileNotFoundError:
            return b""
        except OSError:
            raise CatalogError("catalog_read_failed") from None
        if len(raw) > tool_settings.catalog_bytes:
            raise CatalogError("catalog_size_limit")
        return raw

    def snapshot(self):
        with self.lock:
            raw = self._raw()
            revision = hashlib.sha256(raw).hexdigest()
            if not raw and not self.path.exists():
                return CatalogSnapshot(revision, [])
            try:
                text = raw.decode("utf-8")
                if not text.startswith(PREFIX) or not text.endswith(SUFFIX):
                    raise CatalogError("catalog_markdown_format")
                document = validate_catalog(decode_json(text[len(PREFIX):-len(SUFFIX)]))
            except (CatalogError, ArgumentError, UnicodeError, TypeError):
                raise CatalogError("catalog_invalid", revision) from None
            return CatalogSnapshot(revision, document["automations"])

    def save(self, revision, automations):
        document = validate_catalog({"schema_version": 1, "automations": automations})
        raw_new = (PREFIX + json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
                   + SUFFIX).encode("utf-8")
        if len(raw_new) > tool_settings.catalog_bytes:
            raise CatalogError("catalog_size_limit")
        with self.lock:
            raw = self._raw()
            if revision != hashlib.sha256(raw).hexdigest():
                raise CatalogError("catalog_revision_conflict")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(".automacoes-" + uuid4().hex + ".tmp")
            backup_tmp = self.path.with_name(".automacoes-backup-" + uuid4().hex + ".tmp")
            try:
                with temporary.open("xb") as handle:
                    handle.write(raw_new)
                    handle.flush()
                    os.fsync(handle.fileno())
                if raw:
                    with backup_tmp.open("xb") as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(backup_tmp, self.path.with_suffix(".md.previous"))
                if self._raw() != raw:
                    raise CatalogError("catalog_revision_conflict")
                os.replace(temporary, self.path)
            except OSError:
                raise CatalogError("catalog_write_failed") from None
            finally:
                for item in (temporary, backup_tmp):
                    try:
                        item.unlink(missing_ok=True)
                    except OSError:
                        pass
            return CatalogSnapshot(hashlib.sha256(raw_new).hexdigest(), document["automations"])
