"""Turn audio attachments into text so any model can take a voice message.

Providers such as OpenAI Responses reject audio input, so a channel's voice note is
transcribed first and handed to the agent as a `TextInput`.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ag2.events import BinaryType, TextInput

from assistant.llm_configs import PROVIDER_EXTRA, ProviderLibraryMissing, deps_status
from assistant.observability import log_suppressed

if TYPE_CHECKING:
    from openai import AsyncOpenAI

TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
VOICE_PREFIX = "Voice message transcript:\n\n"
VOICE_UNREADABLE = "The user sent a voice message, but it could not be transcribed."
VOICE_NEEDS_LIBRARY = (
    "The user sent a voice message, but transcribing it needs the openai provider "
    "library; run {install} and tell the user, as voice messages are not read until then."
)

_EXTENSION = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/aac": "aac",
    "audio/aiff": "aiff",
}


def _is_audio(item) -> bool:
    return getattr(item, "kind", None) == BinaryType.AUDIO


async def transcribe_audio(
    items: Sequence,
    *,
    api_key: str,
    client: "AsyncOpenAI | None" = None,
    extras: Mapping[str, tuple[str, str]] = PROVIDER_EXTRA,
) -> list:
    """Replace each audio input with its transcript; other inputs pass through.

    Without an API key the inputs are returned untouched. A clip that fails to
    transcribe becomes a short note, so the turn still runs; without the openai
    library every clip becomes the install hint instead. ``extras`` is the optional-
    library map ``deps_status`` probes (a test states the library situation it means).
    """
    if not api_key or not any(_is_audio(i) for i in items):
        return list(items)
    if client is None:
        status = deps_status("openai", extras=extras)
        if not status["ok"]:
            note = VOICE_NEEDS_LIBRARY.format(install=status["install"])
            log_suppressed("voice transcription", ProviderLibraryMissing(note))
            return [TextInput(note) if _is_audio(i) else i for i in items]
    from openai import AsyncOpenAI  # local: the SDK loads only when a clip is transcribed

    openai = client or AsyncOpenAI(api_key=api_key)
    out = []
    for item in items:
        if not _is_audio(item):
            out.append(item)
            continue
        ext = _EXTENSION.get(item.media_type, "ogg")
        try:
            result = await openai.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL, file=(f"voice.{ext}", item.data, item.media_type)
            )
        except Exception as exc:
            log_suppressed("voice transcription", exc)
            out.append(TextInput(VOICE_UNREADABLE))
            continue
        text = result.text.strip()
        out.append(TextInput(VOICE_PREFIX + text) if text else TextInput(VOICE_UNREADABLE))
    return out
