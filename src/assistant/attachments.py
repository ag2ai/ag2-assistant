"""Build AG2 multimodal inputs from files dropped into a chat.

Channels (Telegram/Discord/Slack) download an attachment's bytes and call
`build_input` to turn them into the right `Input` — `ImageInput` for pictures,
`AudioInput`/`VideoInput` for media, `DocumentInput` for PDFs and everything
else — so the agent can see them via vision/native multimodal. Text files are
inlined as a `TextInput` so they work even on text-only providers.
"""

from pathlib import PurePosixPath

from ag2 import DocumentInput, ImageInput
from ag2.events import AudioInput, TextInput, VideoInput

_IMAGE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_AUDIO = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
}
_VIDEO = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
}
_DOC = {
    ".pdf": "application/pdf",
}
_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".tsv",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".log",
    ".ini",
    ".toml",
    ".sh",
}
_MAX_TEXT_CHARS = 50_000


def _suffix(filename: str) -> str:
    return PurePosixPath(filename or "").suffix.lower()


def build_input(data: bytes, filename: str, media_type: str | None = None):
    """Return an AG2 `Input` for a downloaded attachment, or `None` if empty.

    `filename` drives type detection (its extension); `media_type` from the
    platform is used as a fallback / for the binary inputs that want a MIME type.
    """
    if not data:
        return None
    suffix = _suffix(filename)

    if suffix in _IMAGE:
        return ImageInput(data=data, media_type=media_type or _IMAGE[suffix])
    if suffix in _AUDIO:
        return AudioInput(data=data, media_type=media_type or _AUDIO[suffix])
    if suffix in _VIDEO:
        return VideoInput(data=data, media_type=media_type or _VIDEO[suffix])
    if suffix in _DOC:
        return DocumentInput(data=data, media_type=media_type or _DOC[suffix])

    if suffix in _TEXT_SUFFIXES or (media_type or "").startswith("text/"):
        try:
            text = data.decode("utf-8", errors="replace")[:_MAX_TEXT_CHARS]
        except Exception:
            text = ""
        return TextInput(f"Attached file {filename!r}:\n\n{text}")

    # Fall back to the MIME type when the extension resolved nothing (nameless
    # pasted/dropped files carry a media_type but no ".ext").
    mime = (media_type or "").lower()
    if mime.startswith("image/"):
        return ImageInput(data=data, media_type=media_type)
    if mime.startswith("audio/"):
        return AudioInput(data=data, media_type=media_type)
    if mime.startswith("video/"):
        return VideoInput(data=data, media_type=media_type)
    if mime == "application/pdf":
        return DocumentInput(data=data, media_type=media_type)

    # Unknown/binary: hand it over as a document so the model can still try.
    return DocumentInput(data=data, media_type=media_type or "application/octet-stream")
