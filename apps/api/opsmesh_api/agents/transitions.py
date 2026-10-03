"""Allowed task status changes. Anything else is rejected before it is stored."""

from opsmesh_api.workflows.models import TaskStatus

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.CANCELLED, TaskStatus.SKIPPED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.SKIPPED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.FAILED: frozenset({TaskStatus.READY}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
}

UNSATISFIED: frozenset[TaskStatus] = frozenset(
    {TaskStatus.FAILED, TaskStatus.SKIPPED, TaskStatus.CANCELLED}
)

TERMINAL: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
    }
)


class IllegalTransition(Exception):
    """The requested status change is not in the allowed set."""

    def __init__(self, current: TaskStatus, nxt: TaskStatus) -> None:
        self.current = current
        self.nxt = nxt
        super().__init__(f"cannot move a task from {current.value} to {nxt.value}")


def assert_transition(current: TaskStatus, nxt: TaskStatus) -> None:
    """Raise when `nxt` is not an allowed successor of `current`."""

    if nxt not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(current, nxt)
