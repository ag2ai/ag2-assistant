"""The ACP SDK reports a dead send loop with ``logging.exception`` on the root
logger under no ACP-specific name, and ``MessageSender.send`` then waits on a
future nothing will resolve. The log line is the only warning that a connection
may be wedged, so it has to be attributable rather than anonymous.
"""

import logging

from assistant.acp.sdk_watch import SEND_LOOP_FAILED, watch_send_loop


def _emit_sdk_failure() -> None:
    """What ``acp.task.sender.MessageSender._on_error`` does, verbatim."""
    logging.exception(
        SEND_LOOP_FAILED, exc_info=RuntimeError("cannot reuse already awaited coroutine")
    )


def test_the_sdk_failure_is_re_emitted_under_this_package(caplog):
    with caplog.at_level(logging.ERROR), watch_send_loop():
        _emit_sdk_failure()

    attributed = [r for r in caplog.records if r.name == "assistant.acp.sdk_watch"]
    assert len(attributed) == 1
    assert "send loop" in attributed[0].getMessage().lower()


def test_the_original_record_still_reaches_its_handlers(caplog):
    with caplog.at_level(logging.ERROR), watch_send_loop():
        _emit_sdk_failure()

    assert any(r.getMessage() == SEND_LOOP_FAILED for r in caplog.records)


def test_on_failure_sees_the_record(caplog):
    seen = []
    with caplog.at_level(logging.ERROR), watch_send_loop(seen.append):
        _emit_sdk_failure()

    assert len(seen) == 1
    assert seen[0].getMessage() == SEND_LOOP_FAILED


def test_unrelated_records_pass_through_untouched(caplog):
    seen = []
    with caplog.at_level(logging.ERROR), watch_send_loop(seen.append):
        logging.error("something else entirely")

    assert seen == []
    assert not [r for r in caplog.records if r.name == "assistant.acp.sdk_watch"]


def test_the_filter_is_removed_on_exit(caplog):
    seen = []
    with watch_send_loop(seen.append):
        pass
    with caplog.at_level(logging.ERROR):
        _emit_sdk_failure()

    assert seen == []
