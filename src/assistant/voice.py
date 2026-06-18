"""Realtime voice via AG2's LiveAgent (Gemini Live or OpenAI realtime).

A `LiveAgent` runs a full-duplex realtime session: audio streams in and out
continuously with built-in voice-activity detection and barge-in. We give the
voice agent a *small* set of basic, low-context tools it can handle on its own
(reading tasks, answering a pending question) and one `ask_assistant` tool that
delegates anything heavier — research, Google, creating/scheduling tasks,
detailed work — to the **universal agent**, which owns all the tools and context.
The voice agent then speaks back whatever the assistant returns.

The provider (Gemini or OpenAI) is selected by `AGCLAW_VOICE_PROVIDER` and lives
behind `voice_providers` — this module never branches on it. Audio I/O is
event-based: the browser's mic frames arrive as `RecordedAudioEvent` and the
model's speech leaves as `SynthesizedAudioEvent` on the same conversation stream
(16 kHz mono PCM in / 24 kHz mono PCM out).
"""

import os

from assistant.config import Config
from assistant.voice_providers import PREVIEW_TEXT

# Basic tools the voice agent may run itself: quick, low-context reads/answers.
# Everything else is delegated to the universal agent via ask_assistant.
_BASIC_VOICE_TOOLS = {
    "list_tasks",
    "get_task",
    "list_open_questions",
    "answer_question",
}

VOICE_PROMPT = (
    "You are the voice of AG2 Assistant. You're speaking with the user out loud, so be warm, "
    "natural, and concise — a sentence or two at a time, no markdown, no long "
    "lists read aloud. You are the same assistant they use in the app, just by "
    "voice.\n\n"
    "You can do a few simple things yourself: check their tasks, look up a task's "
    "status, see pending questions, and relay an answer to one.\n\n"
    "For anything beyond that — the weather, news, researching something, the web, "
    "their Google calendar/drive/mail, creating or scheduling a task, or any "
    "current/external info or multi-step work — you MUST call the `ask_assistant` "
    "tool with a clear, complete request. The main assistant will do the heavy "
    "lifting and hand you back an answer; then summarise it for the user in a "
    "natural, spoken way. You do NOT have a weather tool or any other tool of your "
    "own for these — ask_assistant is how you get them done. Never speak JSON, tool "
    "names, or function-call syntax out loud, and never guess external facts; call "
    "`ask_assistant` instead.\n\n"
    "If the user refers to something from earlier in the conversation — \"this "
    "task\", \"that\", \"the one you just made\" — and you don't have the detail, "
    "call `ask_assistant` (it shares the full conversation and will know what they "
    "mean). Don't ask the user to repeat what they already said.\n\n"
    "Otherwise, involve the user as much as needed for clarity: if a request is "
    "genuinely ambiguous, ask a short follow-up out loud before acting. Never "
    "guess at something you could simply ask about.\n\n"
    "When the user clearly signals they're done — they say no to \"anything else?\", "
    "or say goodbye / that's all / nothing else — wrap up in that same turn: give a "
    "brief, warm spoken goodbye AND call `end_call` to hang up. Don't wait for a "
    "second goodbye. Never end the call while they still have something going."
)


def voice_realtime_config(config: Config, voice: str | None = None,
                          provider: str | None = None):
    """Build the realtime RealtimeConfig for the active (or given) provider.

    Delegates to the provider registry — input transcription is enabled per
    provider so the user's speech arrives as text (TranscriptionChunk/Completed)
    for the on-screen bubbles. `voice` defaults to that provider's persisted
    setting. `AGCLAW_VOICE_MODEL` overrides the provider's default model.
    """
    from assistant import voice_providers
    from assistant.settings import get_voice

    p = voice_providers.get(provider)
    model = os.environ.get("AGCLAW_VOICE_MODEL") or p.realtime_model
    return p.build_realtime(config, voice or get_voice(p.name), model)


async def synthesize_preview(config: Config, voice: str, text: str = PREVIEW_TEXT,
                             provider: str | None = None) -> bytes:
    """Single-shot TTS of a sample sentence in `voice`; returns WAV bytes.

    Used by the voice-picker preview and the sample-recording script; delegates
    to the active (or given) provider. A voice the provider's TTS doesn't offer
    raises, and the caller falls back (live preview / skip the sample).
    """
    from assistant import voice_providers

    return await voice_providers.get(provider).synthesize(config, voice, text)


def build_voice_agent(config: Config, tasks, delegate, voice: str | None = None,
                      task_context: str = "", on_end=None, assistant_tools=None):
    """A LiveAgent with a basic tool subset + an ask_assistant delegate tool.

    `tasks` is the TaskService (for the basic read tools); `delegate` is an async
    `(request: str) -> str` that runs the universal agent and returns its reply.
    `task_context`, when the session is opened from a task page, names the task so
    "this task" resolves and is appended to the spoken-agent prompt. `on_end`, when
    given, is a no-arg callback the `end_call` tool fires so the agent can hang up
    the session itself once the conversation is done. `assistant_tools` is the list
    of the universal assistant's tool names — surfaced in the prompt so the voice
    agent knows what it can delegate via ask_assistant.
    """
    from autogen.beta import tool
    from autogen.beta.live import LiveAgent

    from assistant.agent import environment_context
    from assistant.system_tools import build_system_tools

    basic = [t for t in build_system_tools(tasks) if t.name in _BASIC_VOICE_TOOLS]
    # A realtime session's prompt is fixed at connect, so the injected clock would
    # drift on a long call — pair it with a tool the agent can call for fresh time.
    capabilities = (
        "\n\nThe main assistant you reach via `ask_assistant` currently has these "
        "tools: " + ", ".join(assistant_tools) + ". So whenever a request needs any "
        "of them, delegate it — never say you can't do something they can."
        if assistant_tools else ""
    )
    prompt = (
        VOICE_PROMPT
        + "\n\n" + environment_context(config)
        + "\nThis clock is from when the call started; call `current_time` for the "
        "exact time now."
        + capabilities
        + (("\n\n" + task_context) if task_context else "")
    )

    @tool
    def current_time() -> str:
        """The user's current local date, time, timezone, and location right now."""
        return environment_context(config)

    @tool
    async def ask_assistant(request: str) -> str:
        """Hand a request to the main assistant, which has every tool and the full
        context, and return its answer. Use for research, the web, Google
        (calendar/drive/mail), creating or scheduling tasks, or any detailed or
        multi-step work — anything beyond simply reading tasks or answering a
        pending question. Give a complete, self-contained request."""
        try:
            return await delegate(request)
        except Exception as exc:  # keep the voice session alive on a failed turn
            return f"The assistant couldn't complete that: {exc}"

    tools = [*basic, current_time, ask_assistant]

    if on_end is not None:
        @tool
        def end_call() -> str:
            """End the voice call. Use ONLY when the user has clearly indicated they're
            done (e.g. you asked if there's anything else and they said no, or they said
            goodbye). Say a brief, warm goodbye out loud FIRST, then call this."""
            on_end()
            return "Ending the call."

        tools.append(end_call)

    return LiveAgent(
        name="voice",
        prompt=prompt,
        config=voice_realtime_config(config, voice=voice),
        tools=tools,
    )
