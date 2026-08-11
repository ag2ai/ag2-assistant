"""Build AG2 multimodal inputs from files dropped into a chat.

Channels (Telegram/Discord/Slack) download an attachment's bytes and call
`build_input` to turn them into the right `Input` — `ImageInput` for pictures,
`AudioInput`/`VideoInput` for media, `DocumentInput` for PDFs — so the agent can
see them via vision/native multimodal. Text files are inlined as a `TextInput` so
they work even on text-only providers, and anything we cannot type honestly
arrives as a short note naming the file (see `build_input`).
"""

from pathlib import PurePosixPath
from typing import Literal, TypeGuard, get_args

from ag2 import DocumentInput, ImageInput
from ag2.events import AudioInput, TextInput, VideoInput

# Each AG2 input declares its media type as a closed set per kind, and performs no
# runtime validation — a value outside the set travels to the model provider, which
# rejects it, so the user sees a failed turn with no local reproduction. These four
# aliases mirror those sets: our own tables are annotated with them, so a table entry
# AG2 does not accept is a type error here rather than a provider error there.
_ImageType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
_AudioType = Literal[
    "audio/wav", "audio/mpeg", "audio/ogg", "audio/flac", "audio/aiff", "audio/aac"
]
_VideoType = Literal[
    "video/x-matroska",
    "video/quicktime",
    "video/mp4",
    "video/webm",
    "video/x-flv",
    "video/mpeg",
    "video/x-ms-wmv",
    "video/3gpp",
]
# AG2's document set also covers the text-shaped types, but those are inlined as text
# (step 2 below) rather than handed over as documents, so PDF is the only one we build.
_DocumentType = Literal["application/pdf"]

_IMAGE: dict[str, _ImageType] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# No `.m4a`: its media type is an MP4-container audio type that AG2 does not declare
# and none of the three providers accept, so an `.m4a` resolves no kind and lands on
# the note in step 4 (ADR 0010's own fallback chain).
_AUDIO: dict[str, _AudioType] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}
_VIDEO: dict[str, _VideoType] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
}
_DOC: dict[str, _DocumentType] = {
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

_IMAGE_TYPES = frozenset(get_args(_ImageType))
_AUDIO_TYPES = frozenset(get_args(_AudioType))
_VIDEO_TYPES = frozenset(get_args(_VideoType))


def _suffix(filename: str) -> str:
    return PurePosixPath(filename or "").suffix.lower()


# The three guards a platform-supplied media type passes before it is forwarded to a
# constructor. They answer "does AG2 accept this for that kind", which is the same
# question as "will the provider accept it" — AG2's sets were read off the providers'.
def _is_image_type(value: str) -> TypeGuard[_ImageType]:
    return value in _IMAGE_TYPES


def _is_audio_type(value: str) -> TypeGuard[_AudioType]:
    return value in _AUDIO_TYPES


def _is_video_type(value: str) -> TypeGuard[_VideoType]:
    return value in _VIDEO_TYPES


def _unsupported_note(filename: str, media_type: str) -> str:
    """What the agent is told about a file we cannot hand over in its own form."""
    name = f"{filename!r}" if filename else "an unnamed file"
    kind = media_type or "an unrecognised type"
    return (
        f"The user attached {name} ({kind}). Its contents cannot be shown to you in "
        "this form — ask the user about it, or read it with a tool if it is on disk."
    )


def build_input(data: bytes, filename: str, media_type: str | None = None):
    """Return an AG2 `Input` for a downloaded attachment, or `None` if empty.

    Resolution order (ADR 0010: the extension is a strong, stable signal, a platform's
    MIME type is not):

    1. The extension resolves a kind → our table supplies the media type, and the
       platform-supplied value is discarded rather than preferred.
    2. A text extension, or a ``text/*`` type → the contents, inlined.
    3. No kind from the extension but a platform type present → it is forwarded only if
       AG2 accepts it for the kind its prefix implies.
    4. Anything else → a short note naming the file, so the agent knows one arrived and
       can ask about it or reach for a tool.
    """
    if not data:
        return None
    suffix = _suffix(filename)
    mime = (media_type or "").strip().lower()

    if suffix in _IMAGE:
        return ImageInput(data=data, media_type=_IMAGE[suffix])
    if suffix in _AUDIO:
        return AudioInput(data=data, media_type=_AUDIO[suffix])
    if suffix in _VIDEO:
        return VideoInput(data=data, media_type=_VIDEO[suffix])
    if suffix in _DOC:
        return DocumentInput(data=data, media_type=_DOC[suffix])

    if suffix in _TEXT_SUFFIXES or mime.startswith("text/"):
        try:
            text = data.decode("utf-8", errors="replace")[:_MAX_TEXT_CHARS]
        except Exception:
            text = ""
        return TextInput(f"Attached file {filename!r}:\n\n{text}")

    # A nameless pasted/dropped file carries a media type but no ".ext".
    if _is_image_type(mime):
        return ImageInput(data=data, media_type=mime)
    if _is_audio_type(mime):
        return AudioInput(data=data, media_type=mime)
    if _is_video_type(mime):
        return VideoInput(data=data, media_type=mime)
    if mime == "application/pdf":
        return DocumentInput(data=data, media_type="application/pdf")

    return TextInput(_unsupported_note(filename, mime))
