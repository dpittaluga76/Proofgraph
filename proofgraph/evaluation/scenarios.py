from __future__ import annotations

import hashlib
import json
from pathlib import Path

from proofgraph.evaluation.schemas import ScenarioSet

DEFAULT_SCENARIO_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "scenarios.v1.json"


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def scenario_set_hash(scenarios: ScenarioSet) -> str:
    payload = scenarios.model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def load_scenarios(path: Path = DEFAULT_SCENARIO_PATH) -> ScenarioSet:
    return ScenarioSet.model_validate_json(path.read_text(encoding="utf-8"))
