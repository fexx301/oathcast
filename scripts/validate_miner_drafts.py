#!/usr/bin/env python3
"""Validate local Miner draft invariants before official portal validation.

This intentionally does not pretend to be the official Telegraph YAML
validator.  It checks the project-owned invariants and the canonical
OathCast/service contract locally, then reports official portal validation as
``not_run``.  A local pass is not an official registration or portal pass.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PRICE_USDC = Decimal("0.01")
VALIDATION_SCOPE = "draft_local"
OFFICIAL_PORTAL_STATUS = "not_run"
CANONICAL_INTENTS = ["WEATHER_FORECAST"]
WINDOW_CANDIDATE_PATH = (
    ROOT / "miners" / "candidates" / "oathcast-weather-window-unregistered.yaml"
)
WINDOW_CANDIDATE_SLUG = "oathcast-weather-window-unregistered"

EXPECTED_INPUT_PROPERTIES = {
    "event_id": "string",
    "location_name": "string",
    "lat": "number",
    "lon": "number",
    "start": "string",
    "end": "string",
    "cutoff": "string",
    "threshold_mm": "number",
    "operator": "string",
    "provider": "string",
}
EXPECTED_INPUT_REQUIRED = {"lat", "lon", "start", "end"}
EXPECTED_OUTPUT_REQUIRED = {"content", "probability"}
EXPECTED_ENDPOINT_REQUIRED_PARAMS = {
    "lat": "number",
    "lon": "number",
    "start": "string",
    "end": "string",
}
EXPECTED_ENDPOINT_OPTIONAL_PARAMS = {
    "cutoff": "string",
    "threshold_mm": "number",
    "operator": "string",
    "event_id": "string",
    "location_name": "string",
    "provider": "string",
}
EXPECTED_SIGNAL_MAPPING = {
    "label_field": "content",
    "confidence_field": "probability",
    "reason_field": "content",
}
WINDOW_CANDIDATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string"},
        "location_name": {"type": "string"},
        "lat": {"type": "number", "minimum": "-90", "maximum": "90"},
        "lon": {"type": "number", "minimum": "-180", "maximum": "180"},
        "forecast_hours": {
            "type": "integer",
            "minimum": "1",
            "maximum": "24",
        },
        "hourly": {"type": "string", "enum": ["2t"]},
        "provider": {"type": "string", "enum": ["open_meteo"]},
    },
    "required": ["lat", "lon", "forecast_hours", "hourly"],
}
WINDOW_CANDIDATE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "reference_time": {"type": "string", "format": "date-time"},
        "hourly": {
            "type": "object",
            "properties": {
                "time": {
                    "type": "array",
                    "items": {"type": "string", "format": "date-time"},
                },
                "2t": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["time", "2t"],
        },
        "hourly_units": {
            "type": "object",
            "properties": {
                "time": {"type": "string", "enum": ["iso8601"]},
                "2t": {"type": "string", "enum": ["K"]},
            },
            "required": ["time", "2t"],
        },
    },
    "required": ["content", "reference_time", "hourly", "hourly_units"],
}
WINDOW_CANDIDATE_ENDPOINT_REQUIRED_PARAMS = {
    "lat": "number",
    "lon": "number",
    "forecast_hours": "integer",
    "hourly": "string",
}
WINDOW_CANDIDATE_ENDPOINT_OPTIONAL_PARAMS = {
    "event_id": "string",
    "location_name": "string",
    "provider": "string",
}
WINDOW_CANDIDATE_SIGNAL_MAPPING = {
    "label_field": "content",
    "reason_field": "content",
}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _clean_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("#"):
        return ""
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value in {"null", "Null", "NULL", "~"}:
        return None
    return _unquote(value)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_key_value(text: str) -> tuple[str, str] | None:
    if ":" not in text:
        return None
    key, value = text.split(":", 1)
    key = key.strip()
    if not key or key.startswith("-"):
        return None
    return key, value.strip()


def _top_level_scalar(lines: list[str], key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.*?)\s*$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return _unquote(match.group(1))
    return None


def _nested_scalar(lines: list[str], section: str, key: str) -> str | None:
    in_section = False
    section_indent = 0
    for line in lines:
        if re.match(rf"^{re.escape(section)}:\s*$", line):
            in_section = True
            section_indent = _indent(line)
            continue
        if in_section and line.strip() and _indent(line) <= section_indent:
            in_section = False
        if in_section:
            match = re.match(rf"^\s+{re.escape(key)}:\s*(.*?)\s*$", line)
            if match:
                return _unquote(match.group(1))
    return None


def _find_header(
    lines: list[str], key: str, *, indent: int | None = None, start: int = 0
) -> int | None:
    target = f"{key}:"
    for index in range(start, len(lines)):
        line = lines[index]
        if line.strip() != target:
            continue
        if indent is None or _indent(line) == indent:
            return index
    return None


def _direct_scalar(
    lines: list[str], header_index: int, header_indent: int, key: str
) -> Any:
    for line in lines[header_index + 1 :]:
        if line.strip() and _indent(line) <= header_indent:
            break
        if _indent(line) != header_indent + 2:
            continue
        pair = _split_key_value(line.strip())
        if pair and pair[0] == key:
            return _clean_scalar(pair[1])
    return None


def _list_values(lines: list[str], header_index: int, header_indent: int) -> list[Any]:
    values: list[Any] = []
    for line in lines[header_index + 1 :]:
        if line.strip() and _indent(line) <= header_indent:
            break
        stripped = line.strip()
        if _indent(line) == header_indent + 2 and stripped.startswith("-"):
            values.append(_clean_scalar(stripped[1:].strip()))
    return values


def _parse_flow_sequence(value: str) -> list[Any]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [_clean_scalar(item) for item in inner.split(",")]


def _nested_list_values(lines: list[str], section: str, key: str) -> list[Any] | None:
    section_index = _find_header(lines, section, indent=0)
    if section_index is None:
        return None
    section_indent = _indent(lines[section_index])
    for index in range(section_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and _indent(line) <= section_indent:
            break
        if line.strip() == f"{key}:" and _indent(line) == section_indent + 2:
            return _list_values(lines, index, _indent(line))
    return None


def _nested_mapping(
    lines: list[str], section: str, key: str
) -> list[tuple[str, Any]] | None:
    section_index = _find_header(lines, section, indent=0)
    if section_index is None:
        return None
    section_indent = _indent(lines[section_index])
    for index in range(section_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and _indent(line) <= section_indent:
            break
        if line.strip() == f"{key}:" and _indent(line) == section_indent + 2:
            mapping_indent = _indent(line)
            items: list[tuple[str, Any]] = []
            for nested_line in lines[index + 1 :]:
                if nested_line.strip() and _indent(nested_line) <= mapping_indent:
                    break
                if _indent(nested_line) != mapping_indent + 2:
                    continue
                pair = _split_key_value(nested_line.strip())
                if pair:
                    items.append((pair[0], _clean_scalar(pair[1])))
            return items
    return None


def _schema_node(lines: list[str], header_index: int) -> dict[str, Any]:
    header_indent = _indent(lines[header_index])
    contract: dict[str, Any] = {}
    scalar_fields = {"type", "format", "minimum", "maximum"}

    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and _indent(line) <= header_indent:
            break
        if _indent(line) != header_indent + 2:
            continue
        pair = _split_key_value(line.strip())
        if pair is None:
            continue
        key, value = pair
        if key in scalar_fields and value != "":
            contract[key] = _clean_scalar(value)
        elif key in {"required", "enum"} and value == "":
            contract[key] = [
                item
                for item in _list_values(lines, index, _indent(line))
                if isinstance(item, str)
            ]
        elif key == "items" and value == "":
            contract["items"] = _schema_node(lines, index)
        elif key == "properties" and value == "":
            properties: dict[str, Any] = {}
            properties_indent = _indent(line)
            for property_index in range(index + 1, len(lines)):
                property_line = lines[property_index]
                if property_line.strip() and _indent(property_line) <= properties_indent:
                    break
                if _indent(property_line) != properties_indent + 2:
                    continue
                property_pair = _split_key_value(property_line.strip())
                if property_pair is None or property_pair[1] != "":
                    continue
                properties[property_pair[0]] = _schema_node(lines, property_index)
            contract["properties"] = properties
    return contract


def _schema_contract(lines: list[str], section: str) -> dict[str, Any] | None:
    header_index = _find_header(lines, section, indent=0)
    if header_index is None:
        return None
    return _schema_node(lines, header_index)


def load_schema_contract(path: Path, section: str = "output_schema") -> dict[str, Any]:
    """Load the JSON-Schema subset declared by one local Miner manifest."""

    lines = path.resolve().read_text(encoding="utf-8").splitlines()
    schema = _schema_contract(lines, section)
    if schema is None:
        raise ValueError(f"{section} is missing from {_relative_path(path.resolve())}")
    return schema


def _date_time_valid(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def schema_instance_errors(
    instance: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
) -> list[str]:
    """Validate the small schema subset used by the local Miner manifests."""

    errors: list[str] = []
    expected_type = schema.get("type")
    type_valid = True
    if expected_type == "object":
        type_valid = isinstance(instance, dict)
    elif expected_type == "array":
        type_valid = isinstance(instance, list)
    elif expected_type == "string":
        type_valid = isinstance(instance, str)
    elif expected_type == "integer":
        type_valid = isinstance(instance, int) and not isinstance(instance, bool)
    elif expected_type == "number":
        type_valid = (
            isinstance(instance, (int, float, Decimal))
            and not isinstance(instance, bool)
            and math.isfinite(float(instance))
        )
    if not type_valid:
        return [f"{path} must be {expected_type}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")
    if expected_type in {"number", "integer"}:
        numeric = Decimal(str(instance))
        if "minimum" in schema and numeric < Decimal(str(schema["minimum"])):
            errors.append(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and numeric > Decimal(str(schema["maximum"])):
            errors.append(f"{path} must be at most {schema['maximum']}")
    if expected_type == "string" and schema.get("format") == "date-time":
        if not _date_time_valid(instance):
            errors.append(f"{path} must be a timezone-aware date-time")
    if expected_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(
                    schema_instance_errors(item, item_schema, path=f"{path}[{index}]")
                )
    if expected_type == "object":
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                errors.append(f"{path}.{name} is required")
        properties = schema.get("properties", {})
        for name, property_schema in properties.items():
            if name in instance:
                errors.extend(
                    schema_instance_errors(
                        instance[name],
                        property_schema,
                        path=f"{path}.{name}",
                    )
                )
    return errors


def _endpoint_contract(lines: list[str]) -> dict[str, Any] | None:
    header_index = _find_header(lines, "endpoints", indent=0)
    if header_index is None:
        return None
    endpoint: dict[str, Any] = {
        "count": 0,
        "intents": [],
        "params_header_count": 0,
        "query_header_count": 0,
        "param_group_counts": {"required": 0, "optional": 0},
        "params": {"required": [], "optional": []},
        "param_map": {},
    }
    endpoint_indent = _indent(lines[header_index]) + 2
    active_param_group: str | None = None
    current_param: dict[str, Any] | None = None
    in_param_map = False

    def flush_param() -> None:
        nonlocal current_param
        if active_param_group is not None and current_param is not None:
            endpoint["params"][active_param_group].append(current_param)
        current_param = None

    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and _indent(line) <= _indent(lines[header_index]):
            break
        line_indent = _indent(line)
        stripped = line.strip()
        if line_indent == endpoint_indent and stripped.startswith("-"):
            flush_param()
            endpoint["count"] += 1
            pair = _split_key_value(line.strip()[1:].strip())
            if pair and endpoint["count"] == 1:
                endpoint[pair[0]] = _clean_scalar(pair[1])
            continue
        if endpoint["count"] != 1:
            continue
        if line_indent <= endpoint_indent + 2:
            in_param_map = False
        if line_indent == endpoint_indent + 2:
            flush_param()
            active_param_group = None
            pair = _split_key_value(stripped)
            if pair and pair[0] == "params" and pair[1] == "":
                endpoint["params_header_count"] += 1
            elif pair and pair[0] == "param_map" and pair[1] == "":
                in_param_map = True
            elif pair and pair[0] == "intents":
                endpoint["intents"] = _parse_flow_sequence(pair[1])
            elif pair and pair[0] != "params":
                endpoint[pair[0]] = _clean_scalar(pair[1])
        elif line_indent == endpoint_indent + 4 and stripped == "query:":
            endpoint["query_header_count"] += 1
        elif in_param_map and line_indent == endpoint_indent + 4:
            pair = _split_key_value(stripped)
            if pair is not None:
                endpoint["param_map"][pair[0]] = _clean_scalar(pair[1])
        elif line_indent == endpoint_indent + 6 and stripped in {
            "required:",
            "optional:",
        }:
            flush_param()
            active_param_group = stripped[:-1]
            endpoint["param_group_counts"][active_param_group] += 1
        elif (
            active_param_group is not None
            and line_indent == endpoint_indent + 8
            and stripped.startswith("-")
        ):
            flush_param()
            current_param = {}
            pair = _split_key_value(stripped[1:].strip())
            if pair:
                current_param[pair[0]] = _clean_scalar(pair[1])
        elif (
            active_param_group is not None
            and current_param is not None
            and line_indent == endpoint_indent + 10
        ):
            pair = _split_key_value(stripped)
            if pair:
                if pair[0] == "intents":
                    current_param[pair[0]] = _parse_flow_sequence(pair[1])
                else:
                    current_param[pair[0]] = _clean_scalar(pair[1])
    flush_param()
    return endpoint


def _endpoint_param_errors(
    endpoint: dict[str, Any],
    group: str,
    expected: dict[str, str],
    *,
    profile: str = "canonical",
) -> list[str]:
    errors: list[str] = []
    params = endpoint.get("params", {}).get(group, [])
    names = [param.get("name") for param in params]
    expected_names = list(expected)
    if len(names) != len(expected_names) or set(names) != set(expected_names):
        errors.append(
            f"{profile} endpoint {group} query params must be exactly "
            + ", ".join(expected_names)
        )
    for name, expected_type in expected.items():
        matches = [param for param in params if param.get("name") == name]
        if len(matches) != 1:
            continue
        param = matches[0]
        if param.get("type") != expected_type:
            errors.append(
                f"{profile} endpoint query param {name}.type must be {expected_type}"
            )
        if param.get("intents") != CANONICAL_INTENTS:
            errors.append(
                f"{profile} endpoint query param {name} intents must be WEATHER_FORECAST"
            )
    return errors


def _canonical_contract_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    input_schema = _schema_contract(lines, "input_schema")
    output_schema = _schema_contract(lines, "output_schema")

    if input_schema is None:
        errors.append("canonical input_schema is required")
    else:
        if input_schema["type"] != "object":
            errors.append("canonical input_schema.type must be object")
        properties = input_schema["properties"]
        if set(properties) != set(EXPECTED_INPUT_PROPERTIES):
            errors.append(
                "canonical input_schema properties must match the GET service contract"
            )
        for name, expected_type in EXPECTED_INPUT_PROPERTIES.items():
            if properties.get(name, {}).get("type") != expected_type:
                errors.append(f"canonical input_schema.{name}.type must be {expected_type}")
        if set(input_schema["required"]) != EXPECTED_INPUT_REQUIRED:
            errors.append(
                "canonical input_schema.required must be lat, lon, start, and end"
            )
        for name in ("start", "end", "cutoff"):
            if properties.get(name, {}).get("format") != "date-time":
                errors.append(f"canonical input_schema.{name} must use date-time format")
        if properties.get("lat", {}).get("minimum") != "-90":
            errors.append("canonical input_schema.lat.minimum must be -90")
        if properties.get("lat", {}).get("maximum") != "90":
            errors.append("canonical input_schema.lat.maximum must be 90")
        if properties.get("lon", {}).get("minimum") != "-180":
            errors.append("canonical input_schema.lon.minimum must be -180")
        if properties.get("lon", {}).get("maximum") != "180":
            errors.append("canonical input_schema.lon.maximum must be 180")
        if properties.get("threshold_mm", {}).get("enum") != ["0.1"]:
            errors.append("canonical input_schema.threshold_mm must be fixed at 0.1")
        if properties.get("operator", {}).get("enum") != [">"]:
            errors.append('canonical input_schema.operator must be fixed at ">"')

    if output_schema is None:
        errors.append("canonical output_schema is required")
    else:
        if output_schema["type"] != "object":
            errors.append("canonical output_schema.type must be object")
        if output_schema["properties"] != {
            "content": {"type": "string"},
            "probability": {
                "type": "number",
                "minimum": "0",
                "maximum": "1",
            },
        }:
            errors.append("canonical output_schema must match the public content/probability response")
        if set(output_schema["required"]) != EXPECTED_OUTPUT_REQUIRED:
            errors.append("canonical output_schema.required must contain content and probability")

    endpoint = _endpoint_contract(lines)
    if endpoint is None:
        errors.append("canonical endpoint contract is required")
    else:
        if endpoint.get("count") != 1:
            errors.append("canonical draft must define exactly one endpoint")
        if endpoint.get("path") != "/predict":
            errors.append("canonical endpoint path must be /predict")
        if endpoint.get("external_path") != "/v1/forecast/point":
            errors.append("canonical endpoint external_path must be /v1/forecast/point")
        if endpoint.get("method") != "GET":
            errors.append("canonical endpoint method must be GET")
        if endpoint.get("intents") != CANONICAL_INTENTS:
            errors.append(
                "canonical endpoint intents must match semantics: WEATHER_FORECAST only"
            )
        if endpoint.get("params_header_count") != 1:
            errors.append("canonical endpoint must define exactly one params mapping")
        if endpoint.get("query_header_count") != 1:
            errors.append("canonical endpoint params must define exactly one query mapping")
        for group in ("required", "optional"):
            if endpoint.get("param_group_counts", {}).get(group) != 1:
                errors.append(
                    f"canonical endpoint query must define exactly one {group} group"
                )
        errors.extend(
            _endpoint_param_errors(
                endpoint, "required", EXPECTED_ENDPOINT_REQUIRED_PARAMS
            )
        )
        errors.extend(
            _endpoint_param_errors(
                endpoint, "optional", EXPECTED_ENDPOINT_OPTIONAL_PARAMS
            )
        )
        if endpoint.get("param_map") != {
            "lat": "latitude",
            "lon": "longitude",
            "start": "horizon_start",
            "end": "horizon_end",
            "cutoff": "forecast_cutoff",
        }:
            errors.append("canonical endpoint param_map must match the service aliases")

    supported_intents = _nested_list_values(lines, "semantics", "supported_intents")
    if supported_intents != CANONICAL_INTENTS:
        errors.append(
            "canonical semantics.supported_intents must contain exactly WEATHER_FORECAST"
        )

    signal_mapping_items = _nested_mapping(lines, "semantics", "signal_mapping")
    signal_mapping = (
        dict(signal_mapping_items) if signal_mapping_items is not None else None
    )
    if (
        signal_mapping_items is None
        or len(signal_mapping_items) != len(EXPECTED_SIGNAL_MAPPING)
        or len({key for key, _ in signal_mapping_items}) != len(signal_mapping_items)
        or signal_mapping != EXPECTED_SIGNAL_MAPPING
    ):
        errors.append(
            "canonical semantics.signal_mapping must map label=content, "
            "confidence=probability, and reason=content"
        )
    return errors


def _window_candidate_contract_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    input_schema = _schema_contract(lines, "input_schema")
    output_schema = _schema_contract(lines, "output_schema")

    if input_schema != WINDOW_CANDIDATE_INPUT_SCHEMA:
        errors.append(
            "window candidate input_schema must match the 1-to-24-hour 2t request contract"
        )
    if output_schema != WINDOW_CANDIDATE_OUTPUT_SCHEMA:
        errors.append(
            "window candidate output_schema must contain only content, reference_time, hourly, and hourly_units"
        )

    endpoint = _endpoint_contract(lines)
    if endpoint is None:
        errors.append("window candidate endpoint contract is required")
    else:
        if endpoint.get("count") != 1:
            errors.append("window candidate must define exactly one endpoint")
        if endpoint.get("path") != "/predict":
            errors.append("window candidate endpoint path must be /predict")
        if endpoint.get("external_path") != "/v1/forecast/point":
            errors.append(
                "window candidate endpoint external_path must be /v1/forecast/point"
            )
        if endpoint.get("method") != "GET":
            errors.append("window candidate endpoint method must be GET")
        if endpoint.get("intents") != CANONICAL_INTENTS:
            errors.append(
                "window candidate endpoint intents must contain exactly WEATHER_FORECAST"
            )
        if endpoint.get("params_header_count") != 1:
            errors.append("window candidate endpoint must define exactly one params mapping")
        if endpoint.get("query_header_count") != 1:
            errors.append(
                "window candidate endpoint params must define exactly one query mapping"
            )
        for group in ("required", "optional"):
            if endpoint.get("param_group_counts", {}).get(group) != 1:
                errors.append(
                    f"window candidate endpoint query must define exactly one {group} group"
                )
        errors.extend(
            _endpoint_param_errors(
                endpoint,
                "required",
                WINDOW_CANDIDATE_ENDPOINT_REQUIRED_PARAMS,
                profile="window candidate",
            )
        )
        errors.extend(
            _endpoint_param_errors(
                endpoint,
                "optional",
                WINDOW_CANDIDATE_ENDPOINT_OPTIONAL_PARAMS,
                profile="window candidate",
            )
        )
        if endpoint.get("param_map") != {
            "lat": "latitude",
            "lon": "longitude",
        }:
            errors.append(
                "window candidate endpoint param_map must match the service coordinate aliases"
            )

    supported_intents = _nested_list_values(lines, "semantics", "supported_intents")
    if supported_intents != CANONICAL_INTENTS:
        errors.append(
            "window candidate semantics.supported_intents must contain exactly WEATHER_FORECAST"
        )

    signal_mapping_items = _nested_mapping(lines, "semantics", "signal_mapping")
    signal_mapping = (
        dict(signal_mapping_items) if signal_mapping_items is not None else None
    )
    if (
        signal_mapping_items is None
        or len(signal_mapping_items) != len(WINDOW_CANDIDATE_SIGNAL_MAPPING)
        or len({key for key, _ in signal_mapping_items}) != len(signal_mapping_items)
        or signal_mapping != WINDOW_CANDIDATE_SIGNAL_MAPPING
    ):
        errors.append(
            "window candidate semantics.signal_mapping must map label and reason to content without inventing confidence"
        )
    return errors


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def validate_draft(
    path: Path,
    *,
    canonical: bool = False,
    window_candidate: bool = False,
) -> dict[str, Any]:
    path = path.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    version = _top_level_scalar(lines, "version")
    kind = _top_level_scalar(lines, "kind")
    integration_id = _top_level_scalar(lines, "id")
    slug = _top_level_scalar(lines, "slug")
    base_url = _top_level_scalar(lines, "base_url")
    price_text = _nested_scalar(lines, "on_chain", "min_price_usdc")
    is_window_candidate = not canonical and (
        window_candidate
        or path == WINDOW_CANDIDATE_PATH.resolve()
        or slug == WINDOW_CANDIDATE_SLUG
    )

    if version != "1":
        errors.append('version must be "1"')
    if kind != "miner":
        errors.append("kind must be miner")
    if integration_id is None or integration_id == "":
        errors.append("id is required")
    elif not integration_id.isdigit():
        errors.append("id must be numeric")
    if not slug:
        errors.append("slug is required")
    if not base_url:
        errors.append("base_url is required")
    if "auth:" not in lines:
        errors.append("auth block is required")
    if "endpoints:" not in lines:
        errors.append("endpoints block is required")
    if price_text is not None:
        try:
            price = Decimal(price_text)
            if not price.is_finite() or price < MINIMUM_PRICE_USDC:
                errors.append("on_chain.min_price_usdc must be at least 0.01")
        except InvalidOperation:
            errors.append("on_chain.min_price_usdc must be numeric")

    if canonical:
        if slug != "oathcast-weather":
            errors.append("canonical draft must use slug oathcast-weather")
        if not base_url or not base_url.startswith("https://"):
            errors.append("canonical base_url must use HTTPS")
        if base_url and "REPLACE" in base_url:
            errors.append("canonical base_url still contains a placeholder")
        if integration_id is not None and integration_id.isdigit():
            if int(integration_id) <= 0:
                errors.append("canonical id must be a positive numeric routing ID")
            else:
                warnings.append(
                    "canonical numeric ID is only a local routing candidate; it is not the sequential on-chain registrationId"
                )
        if integration_id is not None and integration_id.isdigit() and int(integration_id) > 0:
            warnings.append(
                "official portal validation and endpoint sandbox testing were not run"
            )
        if _nested_scalar(lines, "auth", "type") != "bearer":
            errors.append("canonical auth.type must be bearer")
        if _nested_scalar(lines, "auth", "header_name") != "Authorization":
            errors.append("canonical auth.header_name must be Authorization")
        if _nested_scalar(lines, "auth", "value_prefix") != "Bearer ":
            errors.append('canonical auth.value_prefix must be "Bearer "')
        errors.extend(_canonical_contract_errors(lines))
    elif is_window_candidate:
        if not lines or lines[0] != "# UNREGISTERED COMPATIBILITY CANDIDATE ONLY.":
            errors.append("window candidate must retain the unregistered compatibility marker")
        comments = "\n".join(
            line[1:].strip().lower() for line in lines if line.startswith("#")
        )
        if (
            "additive 1-to-24-hour runtime contract" not in comments
            or "registered oathcast miner contract" not in comments
            or "uploaded, signed" not in comments
            or "substituted for miners/oathcast-weather.yaml" not in comments
        ):
            errors.append(
                "window candidate comments must preserve the additive, unregistered, and non-substitution boundary"
            )
        if integration_id != "0":
            errors.append("window candidate id must remain 0 while unregistered")
        if slug != WINDOW_CANDIDATE_SLUG:
            errors.append(
                f"window candidate draft must use slug {WINDOW_CANDIDATE_SLUG}"
            )
        if not base_url or not base_url.startswith("https://"):
            errors.append("window candidate base_url must use HTTPS")
        if base_url and "REPLACE" in base_url:
            errors.append("window candidate base_url still contains a placeholder")
        if _nested_scalar(lines, "auth", "type") != "bearer":
            errors.append("window candidate auth.type must be bearer")
        if _nested_scalar(lines, "auth", "header_name") != "Authorization":
            errors.append("window candidate auth.header_name must be Authorization")
        if _nested_scalar(lines, "auth", "value_prefix") != "Bearer ":
            errors.append('window candidate auth.value_prefix must be "Bearer "')
        description = _top_level_scalar(lines, "description") or ""
        if "precipitation" in description.lower():
            errors.append("window candidate description must be temperature-only")
        if (
            "unregistered compatibility manifest" not in description.lower()
            or "served additively" not in description.lower()
        ):
            errors.append(
                "window candidate description must state that it is an additive unregistered compatibility manifest"
            )
        errors.extend(_window_candidate_contract_errors(lines))

    official_pending = [
        "official tg-miner-integration portal validation was not run",
    ]
    if canonical:
        official_pending.append(
            "recheck candidate slug availability through the live portal/node; inspect the numeric routing ID conservatively because it is not the on-chain registrationId"
        )
    official_portal_validation = {
        "status": OFFICIAL_PORTAL_STATUS,
        "validated": False,
        "pending": official_pending,
    }
    local_validation = {
        "scope": VALIDATION_SCOPE,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }
    return {
        "path": _relative_path(path),
        "slug": slug,
        "id": integration_id,
        "base_url": base_url,
        "min_price_usdc": price_text,
        "canonical": canonical,
        "window_candidate": is_window_candidate,
        "validation_scope": VALIDATION_SCOPE,
        "local_validation": local_validation,
        "official_portal_validation": official_portal_validation,
        "official_portal_validated": False,
        "errors": errors,
        "warnings": warnings,
        "valid": not errors,
    }


def validate_paths(paths: Iterable[Path], canonical: Path) -> dict[str, Any]:
    canonical = canonical.resolve()
    drafts = [
        validate_draft(
            path,
            canonical=path.resolve() == canonical,
            window_candidate=path.resolve() == WINDOW_CANDIDATE_PATH.resolve(),
        )
        for path in paths
    ]
    errors: list[str] = []
    slugs = [draft["slug"] for draft in drafts if draft["slug"]]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        errors.append(f"duplicate Miner slugs: {', '.join(duplicates)}")
    if not any(draft["canonical"] for draft in drafts):
        errors.append(f"canonical draft not found: {canonical}")
    errors.extend(
        f"{draft['path']}: {error}"
        for draft in drafts
        for error in draft["errors"]
    )
    return {
        "validation_scope": VALIDATION_SCOPE,
        "official_portal_validation": {
            "status": OFFICIAL_PORTAL_STATUS,
            "validated": False,
            "pending": [
                "official tg-miner-integration portal validation was not run",
            ],
        },
        "official_portal_validated": False,
        "valid": not errors,
        "drafts": drafts,
        "errors": errors,
    }


def default_draft_paths() -> list[Path]:
    """Return registered drafts and explicitly local candidate manifests."""

    paths = list((ROOT / "miners").glob("*.yaml"))
    paths.extend((ROOT / "miners" / "candidates").glob("*.yaml"))
    return sorted(paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--canonical",
        type=Path,
        default=ROOT / "miners" / "oathcast-weather.yaml",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = args.paths or default_draft_paths()
    result = validate_paths(paths, args.canonical)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
