# Evaluations

Golden workflows and the pinned smoke list live here.

`evals/golden/workflows.json` is the versioned set. Each workflow names the expected agents, tools, facts, policy outcome, and ceilings. `evals/smoke.json` is the subset the default gate runs.

From `apps/api`, with the simulator fixtures loaded:

```bash
uv run python -m opsmesh_api.evaluation smoke
uv run python -m opsmesh_api.evaluation full
```

Smoke and full stay on the local engine and on recorded traces. They do not call a hosted model. Live model evaluation is opt-in:

```bash
OPSMESH_LIVE_EVAL=1 uv run python -m opsmesh_api.evaluation live
```

This profile refuses that command instead of calling a model. The latest scores for the active tenant are on the console at `/evaluations`.
