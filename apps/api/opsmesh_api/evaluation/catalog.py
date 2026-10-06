"""Load versioned golden workflows from the evals directory."""

import json
from pathlib import Path

from opsmesh_api.evaluation.schema import (
    CATEGORIES,
    GoldenWorkflow,
    MissingFixtureError,
    SuiteDocument,
)
from opsmesh_api.settings import REPO_ROOT

GOLDEN_PATH = REPO_ROOT / "evals" / "golden" / "workflows.json"
SMOKE_PATH = REPO_ROOT / "evals" / "smoke.json"


def load_suite(path: Path | None = None) -> SuiteDocument:
    """Read the golden document. A missing file fails the suite."""

    target = path if path is not None else GOLDEN_PATH
    if not target.is_file():
        raise MissingFixtureError([target.name])
    return SuiteDocument.model_validate_json(target.read_text(encoding="utf-8"))


def load_smoke_ids(path: Path | None = None) -> list[str]:
    """Read the pinned smoke ids. A missing list fails the suite."""

    target = path if path is not None else SMOKE_PATH
    if not target.is_file():
        raise MissingFixtureError([target.name])
    raw = json.loads(target.read_text(encoding="utf-8"))
    ids = raw.get("ids") if isinstance(raw, dict) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids):
        raise MissingFixtureError([target.name])
    return ids


def workflows_by_id(document: SuiteDocument) -> dict[str, GoldenWorkflow]:
    return {item.id: item for item in document.workflows}


def require_ids(document: SuiteDocument, ids: list[str]) -> list[GoldenWorkflow]:
    """Return workflows in list order. Any absent id fails the suite."""

    found = workflows_by_id(document)
    missing = [item for item in ids if item not in found]
    if missing:
        raise MissingFixtureError(missing)
    return [found[item] for item in ids]


def assert_catalog_shape(document: SuiteDocument) -> None:
    """The checked-in set covers every failure mode and is large enough to diff."""

    if len(document.workflows) < 100:
        raise ValueError("the golden set needs at least 100 workflows")
    present = {item.category for item in document.workflows}
    missing = [item for item in CATEGORIES if item not in present]
    if missing:
        raise ValueError("missing categories: " + ", ".join(missing))
    smoke = [item for item in document.workflows if item.gate == "smoke"]
    smoke_categories = [item.category for item in smoke]
    if len(smoke_categories) != len(set(smoke_categories)):
        raise ValueError("each category has one pinned smoke case")
    if set(smoke_categories) != set(CATEGORIES):
        raise ValueError("the smoke set must cover every category")
