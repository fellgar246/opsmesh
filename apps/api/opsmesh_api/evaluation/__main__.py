"""Score the golden set from the command line.

Smoke and full runs stay on the local engine and on recorded traces.
Live model evaluation does not run unless OPSMESH_LIVE_EVAL=1, and this
profile still refuses to call a hosted model.
"""

import sys

from opsmesh_api.db import SessionLocal
from opsmesh_api.evaluation.runner import run_suite
from opsmesh_api.evaluation.schema import LiveEvaluationError, MissingFixtureError


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    suite = args[0] if args else "smoke"
    if suite not in {"smoke", "full", "live"}:
        print("Usage: python -m opsmesh_api.evaluation [smoke|full|live]", file=sys.stderr)
        return 2
    try:
        with SessionLocal() as db:
            report = run_suite(db, suite, persist=True)
    except MissingFixtureError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except LiveEvaluationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        f"{report.suite} {report.suite_version}: "
        f"passed {report.passed} failed {report.failed} "
        f"model_calls {report.efficiency.model_calls}"
    )
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
