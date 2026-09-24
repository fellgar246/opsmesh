"""Rejections raised before a workflow row is inserted."""


class PersistenceRejected(Exception):
    """The record failed validation and was not inserted."""


class WorkflowNotFound(Exception):
    """No workflow records exist for this run and tenant together."""
