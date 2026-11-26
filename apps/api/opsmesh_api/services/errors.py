"""Errors raised by local enterprise services."""


class NotFoundError(Exception):
    """The tenant-scoped record does not exist."""


class StockConflictError(Exception):
    """Reserved units exceed on-hand units. The stored row is left unchanged."""

    def __init__(self, on_hand: int, reserved: int) -> None:
        self.on_hand = on_hand
        self.reserved = reserved
        super().__init__("reserved units exceed on-hand units")


class FixtureError(Exception):
    """Seed data failed validation and was not loaded."""
