# ADR-004: Deterministic policy decides

## Status

Accepted

## Context

Model output is probabilistic. Eligibility, quantity feasibility, and approval requirements are business rules. If generated text can authorize a write, the system cannot explain or repeat a decision.

## Decision

A deterministic policy engine and a deterministic constraint layer decide eligibility, quantity feasibility, and whether approval is required. Agents may gather evidence and explain a rule. Generated text does not authorize a write. Quantities and money use integer minor units: units as integers and money as integer cents.

The canonical rules, expressed in major currency units, are:

1. A purchase total greater than 5000 requires manager approval.
2. A purchase total greater than 25000 requires finance approval. Manager approval still applies.
3. A supplier whose status is not approved cannot be purchased from.
4. A proposed quantity below the supplier minimum order quantity is invalid.
5. A proposed quantity must be a multiple of the supplier case pack. A missing case pack is treated as 1.
6. Extended cost must be less than or equal to the available purchasing budget. If the budget cannot cover the minimum order quantity, the proposal is infeasible.

## Consequences

- Policy outcomes are repeatable without a model call.
- Agents cannot loosen a rule by explaining it differently.
- Persistence and APIs keep money and quantities as integers.
