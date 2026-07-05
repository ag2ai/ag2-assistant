"""Observability: rolling log setup + failure snapshots that capture the error
and the shape of the history that triggered it."""

import json

from assistant.config import load_config
from assistant.observability import capture_failure, log_suppressed, setup_logging


class _Hist:
    def __init__(self, events):
        self._events = events

    async def get_events(self):
        return list(self._events)


class _Stream:
    def __init__(self, events):
        self.history = _Hist(events)


def _cfg(tmp_path):
    cfg = load_config()
    cfg.root_dir = tmp_path / "root"  # the log lives at root_dir (shared across profiles)
    cfg.data_dir = tmp_path / "d"  # debug snapshots are per-profile (data_dir)
    return cfg


def test_setup_logging_creates_logfile_at_root(tmp_path):
    import assistant.observability as obs

    obs._CONFIGURED = False  # setup is idempotent via a module global; reset for this test
    cfg = _cfg(tmp_path)
    logger = setup_logging(cfg)
    logger.info("hello")
    for h in logger.handlers:
        h.flush()
    # single shared log at root_dir, not under the profile's data_dir
    assert (cfg.root_dir / "ag2assistant.log").exists()
    assert not (cfg.data_dir / "ag2assistant.log").exists()


def test_profile_logger_tags_records(tmp_path):
    import assistant.observability as obs

    obs._CONFIGURED = False
    cfg = _cfg(tmp_path)
    setup_logging(cfg)
    adapter = obs.profile_logger("work")
    adapter.info("scoped line")
    for h in obs.logging.getLogger("ag2assistant").handlers:
        h.flush()
    text = (cfg.root_dir / "ag2assistant.log").read_text()
    assert "[work]" in text  # per-profile records are attributable
    assert "scoped line" in text


async def test_capture_failure_writes_record_with_history_shape(tmp_path):
    cfg = _cfg(tmp_path)
    setup_logging(cfg)
    from ag2.events import ModelRequest, ModelResponse

    stream = _Stream([ModelRequest(parts=[]), ModelResponse(message=None)])
    err = ValueError("400 INVALID_ARGUMENT boom")
    path = await capture_failure(
        cfg,
        session_id="task:abc",
        surface="ctx",
        user_text="hi",
        error=err,
        stream=stream,
    )
    assert path is not None
    rec = json.loads(open(path).read())
    assert rec["session_id"] == "task:abc"
    assert rec["error_type"] == "ValueError" and "boom" in rec["error"]
    assert rec["event_count"] == 2
    assert rec["event_types"].get("ModelRequest") == 1
    assert "ModelResponse" in rec["tail"]
    # filename is colon-safe
    assert ":" not in path.split("/")[-1]


async def test_capture_failure_best_effort_no_stream(tmp_path):
    cfg = _cfg(tmp_path)
    setup_logging(cfg)
    path = await capture_failure(cfg, session_id="s1", error=RuntimeError("x"))
    assert path and json.loads(open(path).read())["event_count"] == 0


def test_log_suppressed_records_context(caplog):
    caplog.set_level("WARNING", logger="ag2-assistant")
    err = RuntimeError("emit failed")
    log_suppressed("task event emit", err, task_id="task-1", status="running")

    assert "suppressed failure during task event emit" in caplog.text
    assert "task-1" in caplog.text
    assert "emit failed" in caplog.text
