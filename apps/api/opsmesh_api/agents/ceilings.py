"""Application quotas for one workflow and one model call.

These ceilings are hard limits. Exceeding one fails the workflow with an
explicit limit error. The adapter must not send a larger token budget, and
output past the token ceiling is rejected instead of stored.
"""

MAX_AGENTS = 6
MAX_TASKS = 12
MAX_DELEGATIONS = 10
MAX_SUPERVISOR_ITERATIONS = 4
OUTPUT_TOKEN_CEILING = 900
INPUT_TOKEN_CEILING = 6000


def estimate_tokens(text: str) -> int:
    """Estimate tokens as one per four characters.

    This is a local ceiling check. It is not a provider tokenizer.
    """

    if not text:
        return 0
    return (len(text) + 3) // 4


def _trim_to_tokens(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    max_chars = token_budget * 4 - 3
    if max_chars <= 0:
        return ""
    return text[:max_chars]


def fit_to_input_ceiling(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    """Shorten prompts so the combined estimate stays within the input ceiling."""

    if estimate_tokens(system_prompt) > INPUT_TOKEN_CEILING:
        return _trim_to_tokens(system_prompt, INPUT_TOKEN_CEILING), ""
    remaining = INPUT_TOKEN_CEILING - estimate_tokens(system_prompt)
    if estimate_tokens(user_prompt) <= remaining:
        return system_prompt, user_prompt
    return system_prompt, _trim_to_tokens(user_prompt, remaining)
