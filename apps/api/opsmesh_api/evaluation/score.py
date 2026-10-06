"""Compare an observation to a golden outcome. The local scorer is the source of truth."""

from opsmesh_api.evaluation.schema import (
    SUCCESS_STATUSES,
    CaseScore,
    DeterministicScores,
    EfficiencyScores,
    EvaluationReport,
    ExpectedOutcome,
    FactAccuracy,
    GoldenWorkflow,
    Observation,
)


def score_case(case: GoldenWorkflow, observation: Observation) -> CaseScore:
    """Score one case. A leaked fact or a forbidden tool fails it."""

    expected = case.expected
    failures: list[str] = []
    if observation.status != expected.status:
        failures.append("status")
    if observation.reason != expected.reason:
        failures.append("reason")
    if set(observation.agents) != set(expected.agents):
        failures.append("agents")
    observed_tools = set(observation.tools)
    if not set(expected.tools) <= observed_tools:
        failures.append("tools")
    if observed_tools & set(expected.forbidden_tools):
        failures.append("forbidden_tools")
    if not _facts_match(expected, observation):
        failures.append("facts")
    if not _constraints_match(expected, observation):
        failures.append("constraints")
    if observation.quantity != expected.quantity:
        failures.append("policy")
    if not _approval_matches(expected, observation):
        failures.append("approval")
    if _leaked(expected.foreign_markers, observation.evidence_text):
        failures.append("cross_tenant")
    if not _within_ceilings(expected, observation):
        failures.append("ceilings")
    if expected.require_citations and not _citations_ok(observation):
        failures.append("citations")
    if expected.warning_codes and not set(expected.warning_codes) <= set(observation.warning_codes):
        failures.append("warnings")
    if expected.task_statuses and not _statuses_match(expected.task_statuses, observation):
        failures.append("tasks")
    return CaseScore(
        id=case.id,
        category=case.category,
        tenant_id=case.tenant_id,
        passed=not failures,
        failures=failures,
        status=observation.status,
        model_calls=observation.model_calls,
        tool_calls=observation.tool_calls,
        latency_ms=observation.latency_ms,
        estimated_cost_cents=observation.estimated_cost_cents,
    )


def aggregate(
    suite: str,
    suite_version: str,
    cases: list[GoldenWorkflow],
    scores: list[CaseScore],
    observations: list[Observation],
) -> EvaluationReport:
    """Roll case scores into deterministic metrics and efficiency metrics."""

    by_id = {item.id: item for item in scores}
    ordered = [by_id[item.id] for item in cases]
    paired = zip((item.id for item in cases), observations, strict=True)
    observed = {item_id: observation for item_id, observation in paired}
    correct_agents = 0
    unnecessary = 0
    correct_tools = 0
    forbidden = 0
    fact_matched = 0
    fact_total = 0
    constraint_violations = 0
    policy_violations = 0
    approval_correct = 0
    cross_tenant_failures = 0
    ceilings_ok = True
    for case, score in zip(cases, ordered, strict=True):
        observation = observed[case.id]
        extra = set(observation.agents) - set(case.expected.agents)
        unnecessary += len(extra)
        if "agents" not in score.failures:
            correct_agents += 1
        if "tools" not in score.failures:
            correct_tools += 1
        forbidden += len(set(observation.tools) & set(case.expected.forbidden_tools))
        matched, total = _fact_counts(case.expected, observation)
        fact_matched += matched
        fact_total += total
        if "constraints" in score.failures:
            constraint_violations += 1
        if "policy" in score.failures:
            policy_violations += 1
        if "approval" not in score.failures:
            approval_correct += 1
        if "cross_tenant" in score.failures:
            cross_tenant_failures += 1
        if "ceilings" in score.failures:
            ceilings_ok = False
    passed = sum(1 for item in ordered if item.passed)
    successful = [item for item in ordered if item.status in SUCCESS_STATUSES]
    cost = _cost_per_success(successful)
    return EvaluationReport(
        suite_version=suite_version,
        suite=suite,  # type: ignore[arg-type]
        passed=passed,
        failed=len(ordered) - passed,
        deterministic=DeterministicScores(
            correct_agents=correct_agents,
            unnecessary_agents=unnecessary,
            correct_tools=correct_tools,
            forbidden_tools=forbidden,
            fact_accuracy=FactAccuracy(matched=fact_matched, total=fact_total),
            constraint_violations=constraint_violations,
            policy_violations=policy_violations,
            approval_correct=approval_correct,
            cross_tenant_failures=cross_tenant_failures,
            ceilings_respected=ceilings_ok,
        ),
        efficiency=EfficiencyScores(
            task_completion=len(successful),
            workflows=len(ordered),
            model_calls=sum(item.model_calls for item in ordered),
            tool_calls=sum(item.tool_calls for item in ordered),
            latency_ms=sum(item.latency_ms for item in ordered),
            estimated_cost_per_successful_workflow_cents=cost,
        ),
        cases=ordered,
    )


def _facts_match(expected: ExpectedOutcome, observation: Observation) -> bool:
    matched, total = _fact_counts(expected, observation)
    return matched == total


def _fact_counts(expected: ExpectedOutcome, observation: Observation) -> tuple[int, int]:
    matched = 0
    for key, value in expected.facts.items():
        if observation.facts.get(key) == value:
            matched += 1
    return matched, len(expected.facts)


def _constraints_match(expected: ExpectedOutcome, observation: Observation) -> bool:
    if (
        observation.quantity is not None
        and observation.quantity in expected.rejected_quantities
    ):
        return False
    if expected.binding_constraints is None:
        return True
    return set(observation.binding_constraints) == set(expected.binding_constraints)


def _approval_matches(expected: ExpectedOutcome, observation: Observation) -> bool:
    if observation.requires_manager_approval != expected.requires_manager_approval:
        return False
    if observation.requires_finance_approval != expected.requires_finance_approval:
        return False
    return set(observation.approval_roles) == set(expected.approval_roles)


def _leaked(markers: list[str], evidence_text: str) -> bool:
    haystack = evidence_text.casefold()
    return any(marker.casefold() in haystack for marker in markers if marker)


def _within_ceilings(expected: ExpectedOutcome, observation: Observation) -> bool:
    limits = expected.ceilings
    if len(set(observation.agents)) > limits.max_agents:
        return False
    if observation.task_count > limits.max_tasks:
        return False
    if observation.model_calls > limits.max_model_calls:
        return False
    if observation.tool_calls > limits.max_tool_calls:
        return False
    return observation.latency_ms <= limits.max_latency_ms


def _citations_ok(observation: Observation) -> bool:
    return any(item.strip() for item in observation.citation_checksums)


def _statuses_match(expected: dict[str, str], observation: Observation) -> bool:
    return all(observation.task_statuses.get(agent) == status for agent, status in expected.items())


def efficiency_deltas(
    current: EfficiencyScores,
    previous: EfficiencyScores,
) -> tuple[int, int | None]:
    """Model-call and cost differences. A missing price stays unknown."""

    model_delta = current.model_calls - previous.model_calls
    current_cost = current.estimated_cost_per_successful_workflow_cents
    previous_cost = previous.estimated_cost_per_successful_workflow_cents
    if current_cost is None or previous_cost is None:
        return model_delta, None
    return model_delta, current_cost - previous_cost


def _cost_per_success(successful: list[CaseScore]) -> int | None:
    if not successful:
        return None
    if any(item.estimated_cost_cents is None for item in successful):
        return None
    spent = sum(item.estimated_cost_cents or 0 for item in successful)
    return spent // len(successful)
