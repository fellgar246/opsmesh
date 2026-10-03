"""Failures from an approval decision or a gated submit."""


class ApprovalError(Exception):
    """The decision was refused. The stored approval is unchanged unless noted."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        **extra: object,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)
