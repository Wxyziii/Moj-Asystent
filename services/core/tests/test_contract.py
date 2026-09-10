import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from moj_asystent_core.protocol import ProtocolValidationError, parse_event

PROTOCOL_ROOT = Path(__file__).resolve().parents[3] / "packages" / "protocol"
CASES = json.loads((PROTOCOL_ROOT / "fixtures/contract-cases.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((PROTOCOL_ROOT / "schema/protocol-v1.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_and_json_schema_agree_on_shared_corpus(case: dict) -> None:
    assert VALIDATOR.is_valid(case["value"]) == case["valid"]
    if case["valid"]:
        parsed = parse_event(case["value"])
        assert VALIDATOR.is_valid(parsed.model_dump(mode="json"))
        assert parse_event(parsed.model_dump(mode="json")) == parsed
    else:
        with pytest.raises(ProtocolValidationError):
            parse_event(case["value"])
