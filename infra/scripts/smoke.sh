#!/usr/bin/env bash
# Hit health and one read-only finance path. This script does not place an order.
set -euo pipefail

base="${OPSMESH_BASE_URL:-http://127.0.0.1:8000}"
base="${base%/}"

health="$(curl -fsS "${base}/health")"
python3 -c 'import json,sys; body=json.loads(sys.argv[1]); assert body.get("status")=="ok", body' "${health}"

local_mode="$(python3 -c 'import json,sys; print("yes" if json.loads(sys.argv[1]).get("local_deterministic_simulation") else "no")' "${health}")"
budget_path="${base}/tenants/northwind/finance/budget"

if [[ "${local_mode}" == "yes" ]]; then
  session="$(curl -fsS -X POST "${base}/auth/session" \
    -H 'content-type: application/json' \
    -d '{"tenant_id":"northwind","role":"viewer"}')"
  token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"${session}")"
  budget="$(curl -fsS "${budget_path}" -H "authorization: Bearer ${token}")"
else
  budget="$(curl -fsS "${budget_path}")"
fi

python3 -c 'import json,sys; body=json.loads(sys.argv[1]); assert body.get("tenant_id"); assert isinstance(body.get("available_cents"), int)' "${budget}"
echo "smoke ok ${base}"
