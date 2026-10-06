# ADR-021: Application cost controls

## Status

Accepted

## Context

Fan-out multiplies model and tool calls. A cloud budget alert arrives after the spend, so it cannot stop the next specialist. Operators still need a per-workflow cost and a way to force new work onto the local path.

## Decision

Reorder skips finance and supplier when available stock is already above the selected forecast. Those tasks are stored as skipped with reason `adequate_stock`, and the proposal quantity is 0. Supplier and finance wait until inventory and demand have reported, so they are not started speculatively. Budget plans and supplier-delay reviews still run the specialists those workflows require.

A tenant can start 15 workflows per UTC day. The next start is rejected with `daily_limit`. Input stays within 6000 tokens and output within 900. A token-ceiling failure stops the workflow and does not start another model call. Tool results may be reused inside one workflow when the tenant, tool, and arguments hash match. They are not reused across tenants or workflows. Estimated cost uses the checked-in price table. A model that ran without a listed price is unknown, not zero. `AWS_AGENTIC_FEATURES_ENABLED=false` sends the next specialist call and the next plan to local deterministic simulation. Budget alerts at 1, 3, and 5 USD stay infrastructure signals.

## Consequences

- An overstock reorder does not call finance or supplier.
- The 16th workflow started that day is rejected.
- Turning the agentic cloud switch off produces no further cloud adapter calls.
- Two evaluation runs can be compared on model calls and estimated cost.
