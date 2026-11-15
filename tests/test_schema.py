from __future__ import annotations

import json

from bidcraft.tools.dump_schema import SCHEMA_PATH, build_schema


def test_checked_in_schema_matches_model() -> None:
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == build_schema()


def test_schema_declares_required_sections() -> None:
    schema = build_schema()
    for key in ("facts", "materials", "parameters", "scoring", "requirements", "risks"):
        assert key in schema["properties"]
    fact = schema["$defs"]["FactItem"]
    assert set(fact["required"]) == {"key"}
    assert set(fact["properties"]["status"]["enum"]) == {
        "extracted",
        "confirmed",
        "manual",
        "missing",
        "conflict",
    }
