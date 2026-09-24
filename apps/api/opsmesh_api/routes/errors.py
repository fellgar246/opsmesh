"""Map domain errors to HTTP responses."""

from collections.abc import Callable

from fastapi import HTTPException

from opsmesh_api.services.errors import NotFoundError, StockConflictError


def call[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except StockConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Reserved units exceed on-hand units",
                "on_hand": exc.on_hand,
                "reserved": exc.reserved,
            },
        ) from exc
