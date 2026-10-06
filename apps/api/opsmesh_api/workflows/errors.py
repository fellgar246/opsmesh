"""Rejections raised before a workflow row is inserted."""


class PersistenceRejected(Exception):
    """The record failed validation and was not inserted."""


class WorkflowNotFound(Exception):
    """No workflow records exist for this run and tenant together."""


class DailyLimitError(Exception):
    """The tenant has already started the daily workflow quota."""

    def __init__(self) -> None:
        self.code = "daily_limit"
        self.message = "This tenant has already started 15 workflows today."
        super().__init__(self.message)
