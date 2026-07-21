"""Internal streams (task runs) must not surface in the Chats list."""

from assistant.gateway.core import is_internal_stream


def test_run_and_task_streams_are_internal():
    assert is_internal_stream("task-run:run_abc") is True
    assert is_internal_stream("task:task_abc") is True  # legacy records
    assert is_internal_stream("web-123") is False
    assert is_internal_stream("telegram:42") is False
