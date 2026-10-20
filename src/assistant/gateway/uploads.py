"""Files a client attaches to a message: decoded for the model, saved for the agent."""

import base64

from assistant.attachments import build_input
from assistant.observability import log_suppressed
from assistant.workspace import write_upload


def decode_attachments(items) -> list:
    """Turn UI attachment frames ({name, mime, data:b64}) into AG2 inputs."""
    out = []
    for a in items or []:
        try:
            raw = base64.b64decode(a.get("data", ""))
        except Exception as exc:
            log_suppressed("attachment decode", exc, name=a.get("name"))
            continue
        inp = build_input(raw, a.get("name", "file"), a.get("mime"))
        if inp is not None:
            out.append(inp)
    return out


def persist_uploads(workspace_dir, items) -> list[tuple[str, str]]:
    """Save uploaded files into the workspace (uploads/) so the agent can edit/read
    them by path — returns ``(workspace_path, original_name)`` per saved file."""
    out = []
    for a in items or []:
        try:
            raw = base64.b64decode(a.get("data", ""))
        except Exception as exc:
            log_suppressed("upload decode", exc, name=a.get("name"))
            continue
        if not raw:
            continue
        try:
            name = a.get("name", "file")
            out.append((write_upload(workspace_dir, name, raw), name))
        except Exception as exc:
            log_suppressed("upload persist", exc, name=name)
    return out
