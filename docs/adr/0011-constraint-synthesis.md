# ADR-011: Constraint synthesis

## Status

Accepted

## Context

Specialists can recommend different quantities. A narrative compromise would not be repeatable, and an explanation that names a different quantity or approval would look like authorization.

## Decision

The reorder rule sequence is the only quantity solver. Synthesis passes cited facts into that sequence: available units, the selected forecast, minimum order quantity, case pack, unit cost, available budget, and supplier status. Each input cites an evidence record. A missing number finishes as `missing_evidence` and does not invent a value.

The proposal stores the feasible quantity, the requested quantity, and the constraint codes that changed it. Specialist recommendations stay on the proposal as evidence. Approval roles come from the policy thresholds. The supervisor may supply an English explanation. If that text states another purchase quantity or a different approval requirement, it is replaced with text that follows the outcome. The replaced text is not stored as the accepted explanation.

Creating a proposal marks every other non-superseded proposal on that run as superseded. When several SKUs share a budget, stockout risk rank still decides the funding order. Synthesis only sizes the line that rank selects.

## Consequences

- A demand quantity the budget cannot cover becomes the largest feasible multiple of the case pack, or `insufficient_budget` when the minimum order does not fit.
- An unapproved supplier does not produce a positive quantity.
- A contradictory explanation cannot become the accepted explanation.
- A run does not keep two active proposals.
