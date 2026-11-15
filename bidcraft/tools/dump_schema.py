"""Regenerate the machine-readable fact-ledger JSON Schema from the pydantic model."""

from __future__ import annotations

import json
from pathlib import Path

from bidcraft.schemas import FactLedger

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "schema" / "fact_ledger.schema.json"
)


def build_schema() -> dict:
    schema = FactLedger.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://bidcraft.local/schema/fact_ledger.schema.json"
    schema["title"] = "BidCraft Fact Ledger"
    schema["description"] = (
        "C1 事实台账：生成阶段唯一可信事实来源。由解析+人工确认产生，可直接作为跨工具交换格式。"
    )
    return schema


def dump(path: Path | None = None) -> Path:
    target = path or SCHEMA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    print(dump())
