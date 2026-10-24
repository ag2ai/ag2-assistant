"""Turn audio attachments into text so any model can take a voice message.

Providers such as OpenAI Responses reject audio input, so a channel's voice note is
transcribed first and handed to the agent as a `TextInput`.
"""

from collections.abc import Sequence

from ag2.events import BinaryType, TextInput
from openai import AsyncOpenAI

from assistant.observability import log_suppressed

TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
VOICE_PREFIX = "Voice message transcript:\n\n"
VOICE_UNREADABLE = "The user sent a voice message, but it could not be transcribed."

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
    items: Sequence, *, api_key: str, client: AsyncOpenAI | None = None
) -> list:
    """Replace each audio input with its transcript; other inputs pass through.

    Without an API key the inputs are returned untouched. A clip that fails to
    transcribe becomes a short note, so the turn still runs.
    """
    if not api_key or not any(_is_audio(i) for i in items):
        return list(items)
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
