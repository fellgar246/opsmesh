#!/usr/bin/env bash
# Read the monthly budget and its notification points. Does not change the account.
set -euo pipefail

budget_name="${BUDGET_NAME:-opsmesh-dev-monthly}"
account="$(aws sts get-caller-identity --query Account --output text)"
description="$(aws budgets describe-budget --account-id "${account}" --budget-name "${budget_name}")"
notifications="$(aws budgets describe-notifications-for-budget --account-id "${account}" --budget-name "${budget_name}")"

python3 - "${description}" "${notifications}" <<'PY'
import json
import sys

budget = json.loads(sys.argv[1])
notes = json.loads(sys.argv[2])
amount = str(budget["Budget"]["BudgetLimit"]["Amount"])
if amount not in {"5", "5.0", "5.00"}:
    raise SystemExit(f"expected a 5 USD monthly budget, found {amount}")
thresholds = {float(item["Threshold"]) for item in notes.get("Notifications", [])}
missing = {1.0, 3.0, 5.0} - thresholds
if missing:
    raise SystemExit(f"missing budget notification points: {sorted(missing)}")
print("cost signals ok")
PY
