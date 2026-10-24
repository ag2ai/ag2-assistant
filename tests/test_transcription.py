"""Voice messages reach the agent as text: audio attachments are transcribed first."""

import httpx
from ag2.events import TextInput
from openai import AsyncOpenAI

from assistant.attachments import build_input
from assistant.transcription import VOICE_PREFIX, VOICE_UNREADABLE, transcribe_audio
from tests.support.http import async_client, json_responder


def _openai(handler) -> AsyncOpenAI:
    return AsyncOpenAI(api_key="k", http_client=async_client(handler))


def _voice():
    return build_input(b"OggS....", "voice.ogg", "audio/ogg")


def _texts(items) -> list[str]:
    return [i.content for i in items if isinstance(i, TextInput)]


async def test_a_voice_message_becomes_its_transcript():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " hello there "})

    out = await transcribe_audio([_voice()], api_key="k", client=_openai(handler))

    assert _texts(out) == [VOICE_PREFIX + "hello there"]
    assert b"voice.ogg" in seen[0].content


async def test_other_inputs_pass_through_untouched():
    image = build_input(b"\x89PNG", "pic.png")
    out = await transcribe_audio([image], api_key="k", client=_openai(json_responder({})))
    assert out == [image]


async def test_without_a_key_the_audio_is_left_as_it_was():
    voice = _voice()
    assert await transcribe_audio([voice], api_key="") == [voice]


async def test_a_failed_transcription_becomes_a_note_not_an_error():
    out = await transcribe_audio(
        [_voice()], api_key="k", client=_openai(json_responder({"error": {}}, status_code=500))
    )
    assert _texts(out) == [VOICE_UNREADABLE]
