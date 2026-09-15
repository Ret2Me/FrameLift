from __future__ import annotations

import json
from pathlib import Path

from telemetry_yield.afsk1200_orchestration import (
    API_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from telemetry_yield.fsk_file_orchestration import (
    API_VERSION as FSK_API_VERSION,
    CHECKPOINT_SCHEMA_VERSION as FSK_CHECKPOINT_SCHEMA_VERSION,
    SCHEMA_VERSION as FSK_SCHEMA_VERSION,
)
from telemetry_yield.blind_phase_fsk_file import (
    API_VERSION as BLIND_FSK_API_VERSION,
    SCHEMA_VERSION as BLIND_FSK_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]


def test_afsk1200_result_schema_matches_runtime_contract_constants() -> None:
    schema = json.loads(
        (ROOT / "schemas/afsk1200-file-result-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    properties = schema["properties"]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert properties["api_version"]["const"] == API_VERSION
    assert properties["schema_version"]["const"] == SCHEMA_VERSION
    assert (
        properties["resume"]["properties"]["checkpoint_schema_version"]["const"]
        == CHECKPOINT_SCHEMA_VERSION
    )
    assert schema["additionalProperties"] is False


def test_afsk1200_result_schema_requires_every_runtime_top_level_field() -> None:
    schema = json.loads(
        (ROOT / "schemas/afsk1200-file-result-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == set(schema["properties"])


def test_fsk_result_schema_matches_runtime_contract_constants() -> None:
    schema = json.loads(
        (ROOT / "schemas/fsk-ax25-file-result-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    properties = schema["properties"]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert properties["api_version"]["const"] == FSK_API_VERSION
    assert properties["schema_version"]["const"] == FSK_SCHEMA_VERSION
    assert (
        properties["resume"]["properties"]["checkpoint_schema_version"]["const"]
        == FSK_CHECKPOINT_SCHEMA_VERSION
    )
    assert schema["additionalProperties"] is False


def test_fsk_result_schema_requires_every_runtime_top_level_field() -> None:
    schema = json.loads(
        (ROOT / "schemas/fsk-ax25-file-result-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == set(schema["properties"])


def test_blind_phase_fsk_result_schema_matches_runtime_contract_constants() -> None:
    schema = json.loads(
        (ROOT / f"schemas/{BLIND_FSK_SCHEMA_VERSION}.schema.json").read_text(
            encoding="utf-8"
        )
    )
    properties = schema["properties"]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert properties["api_version"]["const"] == BLIND_FSK_API_VERSION
    assert properties["schema_version"]["const"] == BLIND_FSK_SCHEMA_VERSION
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(properties)


def test_gr_satellites_component_baseline_schema_is_closed_and_complete() -> None:
    schema = json.loads(
        (ROOT / "schemas/gr-satellites-component-baseline-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == (
        "gr-satellites-component-baseline-v1"
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
