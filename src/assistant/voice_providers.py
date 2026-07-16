"""Realtime voice providers (Gemini, OpenAI) behind a tiny registry.

The rest of AG2 Assistant selects a provider by name and asks the registry for its voice
catalogue, its realtime config, or a preview clip — it never branches on the
provider. Add a provider by calling ``register(VoiceProvider(...))``; nothing else
in the codebase changes (no ``if gemini / elif openai`` anywhere).

Each provider lazily imports its SDK inside its builders, so importing this module
(done widely, via ``settings``) stays cheap and keeps the SDKs optional — only the
provider you actually use needs its package installed and key set.
"""

import os
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from assistant.config import Config

DEFAULT_PROVIDER = "gemini"
PREVIEW_TEXT = "Hi, I'm AG2 Assistant. This is how I sound — happy to help you out."


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    """Wrap raw mono 16-bit PCM in a minimal WAV header (for an <audio> preview)."""
    n = len(pcm)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + n)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", n)
    )
    return header + pcm


@dataclass(frozen=True)
class VoiceProvider:
    """Everything AG2 Assistant needs to talk to one realtime voice backend."""

    name: str
    voices: dict[str, str]  # voice name -> short style (also the display order)
    default_voice: str
    realtime_model: str  # default model; AG2ASSISTANT_VOICE_MODEL overrides at the call site
    input_rate: int  # mic capture rate the backend expects (Hz); the browser matches it
    # (config, voice, model, api_key) -> RealtimeConfig. api_key is the resolved key for
    # the active live config (its own per-config key, or the shared provider key); the
    # builders fall back to the provider's env key when it is empty.
    build_realtime: Callable[[Config, str, str, str], object]
    synthesize: Callable[
        [Config, str, str, str], Awaitable[bytes]
    ]  # (config, voice, text, api_key)
    check: Callable[
        [str], Awaitable[None]
    ]  # (api_key) -> None; raises if the key can't reach the API


_REGISTRY: dict[str, VoiceProvider] = {}


def register(p: VoiceProvider) -> None:
    _REGISTRY[p.name] = p


def names() -> tuple[str, ...]:
    """Registered provider names, in registration order."""
    return tuple(_REGISTRY)


def active_provider(persisted: str | None = None) -> str:
    """The active voice provider: `persisted` (a profile's saved choice) →
    AG2ASSISTANT_VOICE_PROVIDER → default. This module never reads settings itself
    (that would need a profile it doesn't know) — the caller passes the profile's
    persisted value from its `Settings`."""
    p = (persisted or "").strip().lower()
    if p in _REGISTRY:
        return p
    p = (os.environ.get("AG2ASSISTANT_VOICE_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return p if p in _REGISTRY else DEFAULT_PROVIDER


def get(name: str | None = None) -> VoiceProvider:
    """The provider by name, or the env/default active one when name is None / unknown.
    Pass a profile's persisted choice (via `Settings.voice_provider()`) as `name` to
    honour per-profile selection; a bare `get()` falls back to env → default only."""
    return _REGISTRY.get(name or active_provider(), _REGISTRY[DEFAULT_PROVIDER])


# --- Gemini -----------------------------------------------------------------

# https://ai.google.dev/gemini-api/docs/speech-generation#voices
_GEMINI_VOICES = {
    "Zephyr": "Bright",
    "Puck": "Upbeat",
    "Charon": "Informative",
    "Kore": "Firm",
    "Fenrir": "Excitable",
    "Leda": "Youthful",
    "Orus": "Firm",
    "Aoede": "Breezy",
    "Callirrhoe": "Easy-going",
    "Autonoe": "Bright",
    "Enceladus": "Breathy",
    "Iapetus": "Clear",
    "Umbriel": "Easy-going",
    "Algieba": "Smooth",
    "Despina": "Smooth",
    "Erinome": "Clear",
    "Algenib": "Gravelly",
    "Rasalgethi": "Informative",
    "Laomedeia": "Upbeat",
    "Achernar": "Soft",
    "Alnilam": "Firm",
    "Schedar": "Even",
    "Gacrux": "Mature",
    "Pulcherrima": "Forward",
    "Achird": "Friendly",
    "Zubenelgenubi": "Casual",
    "Vindemiatrix": "Gentle",
    "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}
_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"


def _gemini_key(api_key: str, config: Config) -> str:
    """Resolved Gemini key: the passed per-config/shared key, else the env fallback."""
    return api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get(config.llm.api_key_env, "")


def _gemini_realtime(config: Config, voice: str, model: str, api_key: str = ""):
    from ag2.live import gemini
    from google.genai import Client

    client = Client(api_key=_gemini_key(api_key, config))
    return gemini.RealTimeConfig(
        model,
        output=gemini.AudioOutput(voice=voice, language_code="en-US"),
        input=gemini.InputConfig(transcribe=True),  # user speech → transcription events
        client=client,
    )


async def _gemini_preview(config: Config, voice: str, text: str, api_key: str = "") -> bytes:
    import asyncio

    from google.genai import Client, types

    client = Client(api_key=_gemini_key(api_key, config))

    def _call() -> bytes:
        resp = client.models.generate_content(
            model=_GEMINI_TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
            ),
        )
        return resp.candidates[0].content.parts[0].inline_data.data

    pcm = await asyncio.to_thread(_call)
    return pcm_to_wav(pcm)


async def _gemini_check(api_key: str) -> None:
    """Cheap key probe: list models (one page). Raises on a bad/absent key."""
    import asyncio

    from google.genai import Client

    client = Client(api_key=api_key or os.environ.get("GEMINI_API_KEY", ""))
    await asyncio.to_thread(lambda: next(iter(client.models.list()), None))


# --- OpenAI -----------------------------------------------------------------

# marin/cedar are OpenAI's recommended voices, so they lead and marin is default.
_OPENAI_VOICES = {
    "marin": "Natural (recommended)",
    "cedar": "Smooth (recommended)",
    "alloy": "Neutral",
    "ash": "Expressive",
    "ballad": "Warm",
    "coral": "Bright",
    "echo": "Calm",
    "sage": "Mellow",
    "shimmer": "Gentle",
    "verse": "Versatile",
}
_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
# OpenAI realtime is natively 24 kHz in/out — the browser captures at this rate.
_OPENAI_INPUT_RATE = 24000


def _openai_key(api_key: str = "") -> str:
    return api_key or os.environ.get("OPENAI_API_KEY", "")


def _openai_realtime(config: Config, voice: str, model: str, api_key: str = ""):
    from ag2.live import openai as oai
    from openai import AsyncOpenAI

    # Minimal config matching AG2's known-working tool-calling example: model + voice,
    # everything else (24 kHz audio, semantic-VAD turn detection) left at the defaults.
    return oai.RealTimeConfig(
        model,
        output=oai.AudioOutput(voice=voice),
        client=AsyncOpenAI(api_key=_openai_key(api_key)),
    )


async def _openai_preview(config: Config, voice: str, text: str, api_key: str = "") -> bytes:
    from ag2.live import OpenAITTSConfig
    from openai import AsyncOpenAI

    tts = OpenAITTSConfig(
        _OPENAI_TTS_MODEL, voice=voice, client=AsyncOpenAI(api_key=_openai_key(api_key))
    )
    pcm = await tts.synthesize(text)  # 24 kHz mono PCM
    return pcm_to_wav(pcm, rate=24000)


async def _openai_check(api_key: str) -> None:
    """Cheap key probe: list models. Raises on a bad/absent key."""
    from openai import AsyncOpenAI

    await AsyncOpenAI(api_key=_openai_key(api_key)).models.list()


# --- registration -----------------------------------------------------------

register(
    VoiceProvider(
        name="gemini",
        voices=_GEMINI_VOICES,
        default_voice="Puck",
        realtime_model="gemini-3.1-flash-live-preview",
        input_rate=16000,  # Gemini Live is fixed at 16 kHz mono PCM input
        build_realtime=_gemini_realtime,
        synthesize=_gemini_preview,
        check=_gemini_check,
    )
)

register(
    VoiceProvider(
        name="openai",
        voices=_OPENAI_VOICES,
        default_voice="marin",
        realtime_model="gpt-realtime-2",  # the model AG2's docs/examples use for reliable tool calling
        input_rate=_OPENAI_INPUT_RATE,  # 24 kHz native
        build_realtime=_openai_realtime,
        synthesize=_openai_preview,
        check=_openai_check,
    )
)
