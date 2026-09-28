"""Attribute the ACP SDK's send-loop failure, which it reports anonymously.

``acp.task.sender.MessageSender._on_error`` calls ``logging.exception`` on the
root logger under no ACP-specific name, so the failure is indistinguishable from
any other stray line. It matters because ``send`` awaits a future only the send
loop resolves: once that loop is dead, every later send on the connection waits
forever rather than failing, so this record is the only warning that a connection
may be wedged.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator

SEND_LOOP_FAILED = "Send loop failed"

_log = logging.getLogger(__name__)


class _AttributeSendLoopFailure(logging.Filter):
    """Root-logger filter that reports the SDK's record and always passes it on."""

    def __init__(self, report: Callable[[logging.LogRecord], None]) -> None:
        super().__init__()
        self._report = report

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage() == SEND_LOOP_FAILED:
            self._report(record)
        return True


@contextlib.contextmanager
def watch_send_loop(
    on_failure: Callable[[logging.LogRecord], None] | None = None,
) -> Iterator[None]:
    """Re-emit the SDK's send-loop failure under this package's logger for the block.

    Matched on the root logger, where the SDK emits it; the original record is
    passed through untouched so nothing downstream loses it.
    """

    def report(record: logging.LogRecord) -> None:
        _log.error(
            "ACP SDK send loop died — later sends on this connection cannot complete",
            exc_info=record.exc_info,
        )
        if on_failure is not None:
            on_failure(record)

    root = logging.getLogger()
    installed = _AttributeSendLoopFailure(report)
    root.addFilter(installed)
    try:
        yield
    finally:
        root.removeFilter(installed)
