"""Google tools for AG2 Assistant — Gmail, Calendar, Drive.

Read/search tools run freely; anything that writes or sends is wrapped with the
human-approval middleware, so e.g. sending an email always shows a HITL approval
card first and is denied if there's no one to ask.

All Google API calls are lazy and run in a thread (the client is blocking). The
agent only gets these tools when the user is signed in (`ag2-assistant google login`).
"""

import asyncio
import base64
import io
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Annotated

from ag2 import tool
from pydantic import Field

from assistant.integrations.google_auth import build_service
from assistant.tools.approval import require_command_approval

_MAX = 10


def _svc(api: str, version: str):
    return build_service(api, version)


# --------------------------------------------------------------------------- Gmail


@tool
async def gmail_search(
    query: Annotated[str, Field(description="Gmail search query, e.g. 'from:alice is:unread'.")],
    max_results: Annotated[int, Field(description="Max messages to return.")] = _MAX,
) -> str:
    """Search the user's Gmail and return matching messages (id, from, subject, date, snippet)."""

    def _run():
        svc = _svc("gmail", "v1")
        resp = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        out = []
        for m in resp.get("messages", []):
            full = (
                svc.users()
                .messages()
                .get(
                    userId="me",
                    id=m["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            hdrs = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            out.append(
                {
                    "id": m["id"],
                    "from": hdrs.get("From", ""),
                    "subject": hdrs.get("Subject", ""),
                    "date": hdrs.get("Date", ""),
                    "snippet": full.get("snippet", ""),
                    "unread": "UNREAD" in (full.get("labelIds") or []),
                }
            )
        return out

    try:
        results = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Gmail search failed: {exc}"
    if not results:
        return f"No messages matched {query!r}."
    lines = [
        f"- [{r['id']}] {r['from']} — {r['subject']} ({r['date']})"
        f"{' [unread]' if r['unread'] else ''}\n  {r['snippet']}\n"
        f"  link=https://mail.google.com/mail/u/0/#all/{r['id']}"
        for r in results
    ]
    return "Messages:\n" + "\n".join(lines)


@tool
async def gmail_read(
    message_id: Annotated[str, Field(description="The Gmail message id (from gmail_search).")],
) -> str:
    """Read a full Gmail message by id (headers + plain-text body)."""

    def _decode(payload):
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        for part in payload.get("parts", []) or []:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
        for part in payload.get("parts", []) or []:
            text = _decode(part)
            if text:
                return text
        return ""

    def _run():
        svc = _svc("gmail", "v1")
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = _decode(msg.get("payload", {}))
        return hdrs, body

    try:
        hdrs, body = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Could not read message {message_id}: {exc}"
    return (
        f"From: {hdrs.get('From', '')}\nTo: {hdrs.get('To', '')}\n"
        f"Date: {hdrs.get('Date', '')}\nSubject: {hdrs.get('Subject', '')}\n\n{body.strip()}"
    )


@tool(middleware=[require_command_approval()])
async def gmail_send(
    to: Annotated[str, Field(description="Recipient email address.")],
    subject: Annotated[str, Field(description="Email subject.")],
    body: Annotated[str, Field(description="Plain-text email body.")],
) -> str:
    """Send an email from the user's Gmail. Requires human approval before sending."""

    def _run():
        svc = _svc("gmail", "v1")
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return sent.get("id", "")

    try:
        msg_id = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Failed to send email: {exc}"
    return f"Email sent to {to} (id {msg_id})."


@tool
async def gmail_create_draft(
    to: Annotated[str, Field(description="Recipient email address.")],
    subject: Annotated[str, Field(description="Email subject.")],
    body: Annotated[str, Field(description="Plain-text email body.")],
) -> str:
    """Save an email as a Gmail draft (does NOT send — the user can review and send it)."""

    def _run():
        svc = _svc("gmail", "v1")
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        draft = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return draft.get("id", "")

    try:
        draft_id = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Failed to create draft: {exc}"
    return f"Draft saved (id {draft_id}). Review it in Gmail and send when ready."


# ------------------------------------------------------------------------ Calendar


@tool
async def calendar_list_events(
    time_min: Annotated[
        str | None, Field(description="ISO start, e.g. 2026-06-14T00:00:00Z. Default now.")
    ] = None,
    time_max: Annotated[str | None, Field(description="ISO end. Optional.")] = None,
    max_results: Annotated[int, Field(description="Max events.")] = _MAX,
) -> str:
    """List upcoming Google Calendar events in a time window."""

    def _run():
        svc = _svc("calendar", "v3")
        params = {
            "calendarId": "primary",
            "timeMin": time_min or datetime.now(timezone.utc).isoformat(),
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max:
            params["timeMax"] = time_max
        return svc.events().list(**params).execute().get("items", [])

    try:
        events = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Could not list events: {exc}"
    if not events:
        return "No events found."
    lines = []
    for e in events:
        start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
        end = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date", "")
        all_day = "dateTime" not in e.get("start", {})
        span = f"{start} (all day)" if all_day else f"{start} → {end}"
        loc = f" @ {e['location']}" if e.get("location") else ""
        join = e.get("hangoutLink") or ""
        join = f" join={join}" if join else ""
        link = f" link={e['htmlLink']}" if e.get("htmlLink") else ""
        lines.append(
            f"- {span} — {e.get('summary', '(no title)')}{loc}{join}{link} [{e.get('id', '')}]"
        )
    return "Events:\n" + "\n".join(lines)


@tool(middleware=[require_command_approval()])
async def calendar_create_event(
    summary: Annotated[str, Field(description="Event title.")],
    start: Annotated[str, Field(description="ISO start datetime, e.g. 2026-06-20T14:00:00+10:00.")],
    end: Annotated[str, Field(description="ISO end datetime.")],
    description: Annotated[str | None, Field(description="Optional details.")] = None,
) -> str:
    """Create a Google Calendar event. Requires human approval first."""

    def _run():
        svc = _svc("calendar", "v3")
        body = {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if description:
            body["description"] = description
        ev = svc.events().insert(calendarId="primary", body=body).execute()
        return ev.get("htmlLink", "")

    try:
        link = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Failed to create event: {exc}"
    return f"Event created: {link}"


# --------------------------------------------------------------------------- Drive


@tool
async def drive_search(
    query: Annotated[str, Field(description="Drive full-text query, e.g. 'quarterly report'.")],
    max_results: Annotated[int, Field(description="Max files.")] = _MAX,
) -> str:
    """Search the user's Google Drive and return matching files (id, name, type)."""

    def _run():
        svc = _svc("drive", "v3")
        resp = (
            svc.files()
            .list(
                q=f"fullText contains '{query}'" if query else None,
                pageSize=max_results,
                fields="files(id, name, mimeType, modifiedTime)",
            )
            .execute()
        )
        return resp.get("files", [])

    try:
        files = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Drive search failed: {exc}"
    if not files:
        return f"No files matched {query!r}."
    return "Files:\n" + "\n".join(f"- [{f['id']}] {f['name']} ({f['mimeType']})" for f in files)


def _extract_drive_id(value: str) -> str:
    """Accept a raw Drive file id or a Docs/Sheets/Drive URL and return the id."""
    if "http" in value or "/" in value:
        m = re.search(r"/d/([a-zA-Z0-9_-]+)", value) or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", value)
        if m:
            return m.group(1)
    return value.strip()


# Non-Google mime types we can safely decode as text (plus any text/*).
_TEXTUAL_MIMES = frozenset(
    {"application/json", "application/xml", "application/csv", "application/x-yaml"}
)


def _pdf_text(data: bytes) -> str | None:
    """Extract a PDF's text, or None if it has no extractable text (e.g. scanned)."""
    from pypdf import PdfReader  # local: optional [google] extra

    pages = [(page.extract_text() or "").strip() for page in PdfReader(io.BytesIO(data)).pages]
    return "\n\n".join(p for p in pages if p) or None


def _decode_drive_content(name: str, mime: str, data: bytes) -> str:
    """Turn downloaded Drive bytes into model-safe text. Binary formats are never
    decoded raw (mojibake poisons the conversation) — PDFs get real text
    extraction; anything else binary gets an honest 'can't read this' message."""
    if mime == "application/pdf":
        try:
            text = _pdf_text(data)
        except Exception:
            text = None
        if text is None:
            return (
                f"{name} is a PDF with no extractable text (likely scanned images) — "
                "it can't be read as text."
            )
        return text
    if mime.startswith("text/") or mime in _TEXTUAL_MIMES:
        return data.decode("utf-8", "replace")
    return (
        f"{name} is a binary file ({mime}) — it can't be read as text. "
        "Tell the user what it is and suggest they open it in Google Drive."
    )


@tool
async def drive_read(
    file_id: Annotated[
        str, Field(description="The Drive file id (from drive_search) or a Docs/Sheets/Drive URL.")
    ],
) -> str:
    """Read a Google Drive file's text content (Docs/Sheets are exported as text,
    PDF text is extracted; other binary formats are reported, not decoded).

    Accepts a file id or a full Google Docs/Sheets/Drive link.
    """
    fid = _extract_drive_id(file_id)

    def _run():
        svc = _svc("drive", "v3")
        meta = svc.files().get(fileId=fid, fields="name, mimeType").execute()
        name, mime = meta.get("name", fid), meta.get("mimeType", "")
        if mime.startswith("application/vnd.google-apps"):
            export_as = "text/csv" if "spreadsheet" in mime else "text/plain"
            data = svc.files().export(fileId=fid, mimeType=export_as).execute()
            text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        else:
            data = svc.files().get_media(fileId=fid).execute()
            data = data if isinstance(data, bytes) else str(data).encode()
            text = _decode_drive_content(name, mime, data)
        return name, text[:50_000]

    try:
        name, text = await asyncio.to_thread(_run)
    except Exception as exc:
        return f"Could not read file {file_id}: {exc}"
    return f"Contents of {name}:\n\n{text}"


def build_google_tools() -> list:
    """All Google tools, for inclusion when the user is signed in."""
    return [
        gmail_search,
        gmail_read,
        gmail_send,
        gmail_create_draft,
        calendar_list_events,
        calendar_create_event,
        drive_search,
        drive_read,
    ]


__all__ = ["build_google_tools"]
