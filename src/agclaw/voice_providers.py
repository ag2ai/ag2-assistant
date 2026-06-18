"""Realtime voice providers (Gemini, OpenAI) behind a tiny registry.

The rest of AGClaw selects a provider by name and asks the registry for its voice
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

from agclaw.config import Config

DEFAULT_PROVIDER = "gemini"
PREVIEW_TEXT = "Hi, I'm AGClaw. This is how I sound — happy to help you out."


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    """Wrap raw mono 16-bit PCM in a minimal WAV header (for an <audio> preview)."""
    n = len(pcm)
    header = b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16
    ) + b"data" + struct.pack("<I", n)
    return header + pcm


@dataclass(frozen=True)
class VoiceProvider:
    """Everything AGClaw needs to talk to one realtime voice backend."""

    name: str
    voices: dict[str, str]          # voice name -> short style (also the display order)
    default_voice: str
    realtime_model: str             # default model; AGCLAW_VOICE_MODEL overrides at the call site
    input_rate: int                 # mic capture rate the backend expects (Hz); the browser matches it
    build_realtime: Callable[[Config, str, str], object]            # (config, voice, model) -> RealtimeConfig
    synthesize: Callable[[Config, str, str], Awaitable[bytes]]      # (config, voice, text) -> WAV bytes


_REGISTRY: dict[str, VoiceProvider] = {}


def register(p: VoiceProvider) -> None:
    _REGISTRY[p.name] = p


def names() -> tuple[str, ...]:
    """Registered provider names, in registration order."""
    return tuple(_REGISTRY)


def active_provider() -> str:
    """The active voice provider: persisted UI setting → AGCLAW_VOICE_PROVIDER → default.
    The settings read is a lazy import to avoid a circular import at module load."""
    try:
        from agclaw import settings

        p = (settings.get_voice_provider() or "").strip().lower()
        if p in _REGISTRY:
            return p
    except Exception:
        pass
    p = (os.environ.get("AGCLAW_VOICE_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return p if p in _REGISTRY else DEFAULT_PROVIDER


def get(name: str | None = None) -> VoiceProvider:
    """The provider by name, or the active one when name is None / unknown."""
    return _REGISTRY.get(name or active_provider(), _REGISTRY[DEFAULT_PROVIDER])


# --- Gemini -----------------------------------------------------------------

# https://ai.google.dev/gemini-api/docs/speech-generation#voices
_GEMINI_VOICES = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm",
    "Fenrir": "Excitable", "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy",
    "Callirrhoe": "Easy-going", "Autonoe": "Bright", "Enceladus": "Breathy",
    "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth",
    "Despina": "Smooth", "Erinome": "Clear", "Algenib": "Gravelly",
    "Rasalgethi": "Informative", "Laomedeia": "Upbeat", "Achernar": "Soft",
    "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature",
    "Pulcherrima": "Forward", "Achird": "Friendly", "Zubenelgenubi": "Casual",
    "Vindemiatrix": "Gentle", "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}
_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"


def _gemini_realtime(config: Config, voice: str, model: str):
    from google.genai import Client

    from autogen.beta.live import gemini

    client = Client(api_key=os.environ.get(config.llm.api_key_env, ""))
    return gemini.RealTimeConfig(
        model,
        output=gemini.AudioOutput(voice=voice, language_code="en-US"),
        input=gemini.InputConfig(transcribe=True),   # user speech → transcription events
        client=client,
    )


async def _gemini_preview(config: Config, voice: str, text: str) -> bytes:
    import asyncio

    from google.genai import Client, types

    client = Client(api_key=os.environ.get(config.llm.api_key_env, ""))

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


# --- OpenAI -----------------------------------------------------------------

# marin/cedar are OpenAI's recommended voices, so they lead and marin is default.
_OPENAI_VOICES = {
    "marin": "Natural (recommended)", "cedar": "Smooth (recommended)",
    "alloy": "Neutral", "ash": "Expressive", "ballad": "Warm", "coral": "Bright",
    "echo": "Calm", "sage": "Mellow", "shimmer": "Gentle", "verse": "Versatile",
}
_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
# OpenAI realtime is natively 24 kHz in/out — the browser captures at this rate.
_OPENAI_INPUT_RATE = 24000


def _openai_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "")


def _openai_realtime(config: Config, voice: str, model: str):
    from openai import AsyncOpenAI

    from autogen.beta.live import openai as oai

    # Minimal config matching AG2's known-working tool-calling example: model + voice,
    # everything else (24 kHz audio, semantic-VAD turn detection) left at the defaults.
    return oai.RealTimeConfig(
        model,
        output=oai.AudioOutput(voice=voice),
        client=AsyncOpenAI(api_key=_openai_key()),
    )


async def _openai_preview(config: Config, voice: str, text: str) -> bytes:
    from openai import AsyncOpenAI

    from autogen.beta.live import OpenAITTSConfig

    tts = OpenAITTSConfig(_OPENAI_TTS_MODEL, voice=voice,
                          client=AsyncOpenAI(api_key=_openai_key()))
    pcm = await tts.synthesize(text)   # 24 kHz mono PCM
    return pcm_to_wav(pcm, rate=24000)


# --- registration -----------------------------------------------------------

register(VoiceProvider(
    name="gemini",
    voices=_GEMINI_VOICES,
    default_voice="Puck",
    realtime_model="gemini-3.1-flash-live-preview",
    input_rate=16000,   # Gemini Live is fixed at 16 kHz mono PCM input
    build_realtime=_gemini_realtime,
    synthesize=_gemini_preview,
))

register(VoiceProvider(
    name="openai",
    voices=_OPENAI_VOICES,
    default_voice="marin",
    realtime_model="gpt-realtime-2",   # the model AG2's docs/examples use for reliable tool calling
    input_rate=_OPENAI_INPUT_RATE,   # 24 kHz native
    build_realtime=_openai_realtime,
    synthesize=_openai_preview,
))
