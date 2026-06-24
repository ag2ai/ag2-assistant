"""Lightweight, file-based observability so breakages are diagnosable after the
fact (without reproducing).

Three layers, all writing where they can be read back:
- A rolling log at ``<data_dir>/ag2assistant.log`` (AG2 Assistant + AG2's own `autogen.*` logs).
- AG2-native ``LoggingMiddleware`` on each agent → per-turn LLM call / tool / turn
  entries in that same log.
- A failure snapshot: when an agent turn raises (e.g. a provider 400), a compact
  JSON record at ``<data_dir>/debug/<ts>-<session>.json`` capturing the error,
  traceback, and the *shape* of the history that triggered it.

The full per-turn event stream is already persisted by the gateway's
``EventLogWriter`` (the deep record); these add the human-readable trail and the
failure snapshots that point at it.
"""

import json
import logging
import logging.handlers
import traceback
from collections import Counter
from datetime import datetime
from typing import Any

_CONFIGURED = False


def setup_logging(config) -> logging.Logger:
    """Initialise rolling file logging (idempotent). Returns the 'ag2assistant' logger."""
    global _CONFIGURED
    logger = logging.getLogger("ag2assistant")
    if _CONFIGURED:
        return logger
    logger.setLevel(logging.INFO)
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        data_dir / "ag2assistant.log", maxBytes=2_000_000, backupCount=3
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # fold AG2's own logs (compaction/aggregation/event-log failures, etc.) in too
    ag2 = logging.getLogger("autogen")
    ag2.addHandler(fh)
    ag2.setLevel(logging.INFO)
    _CONFIGURED = True
    logger.info("logging initialised → %s", data_dir / "ag2assistant.log")
    return logger


def agent_logging_middleware():
    """AG2-native LoggingMiddleware routed to the ag2assistant logger (per-turn LLM/tool)."""
    from autogen.beta.middleware import LoggingMiddleware

    return LoggingMiddleware(logger=logging.getLogger("ag2assistant.agent"))


def log_suppressed(operation: str, exc: BaseException, **context: Any) -> None:
    """Record a best-effort-path failure that should not abort the caller."""
    detail = " ".join(f"{key}={value!r}" for key, value in context.items() if value is not None)
    msg = f"suppressed failure during {operation}"
    if detail:
        msg = f"{msg} ({detail})"
    logging.getLogger("ag2assistant").warning(msg, exc_info=(type(exc), exc, exc.__traceback__))


async def capture_failure(
    config, *, session_id, surface="", user_text="", error=None, stream=None
) -> str | None:
    """Write a compact JSON debug record for a failed turn; return its path.

    Captures the error + traceback and the *shape* of the history (event-type
    counts + tail), which is usually enough to spot malformed sequences (e.g. an
    orphaned tool cycle) without re-running. Best-effort; never raises.
    """
    logger = logging.getLogger("ag2assistant")
    try:
        events = []
        if stream is not None:
            try:
                events = list(await stream.history.get_events())
            except Exception:
                events = []
        record = {
            "ts": datetime.now().astimezone().isoformat(),
            "session_id": session_id,
            "surface": (surface or "")[:800],
            "user_text": (user_text or "")[:800],
            "error_type": type(error).__name__ if error else "",
            "error": str(error)[:3000],
            "traceback": (
                "".join(traceback.format_exception(type(error), error, error.__traceback__))[-4000:]
                if error
                else ""
            ),
            "event_count": len(events),
            "event_types": dict(Counter(type(e).__name__ for e in events)),
            "tail": [type(e).__name__ for e in events[-15:]],
        }
        debug_dir = config.data_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        safe = str(session_id).replace(":", "_").replace("/", "_")
        path = debug_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe}.json"
        path.write_text(json.dumps(record, indent=2))
        logger.error(
            "turn failed (%s) on %s → debug record %s", record["error_type"], session_id, path
        )
        return str(path)
    except Exception:
        logger.exception("failed to write failure record")
        return None
