"""Realtime voice via AG2's LiveAgent (Gemini Live).

A `LiveAgent` runs a full-duplex Gemini Live session: audio streams in and out
continuously with built-in voice-activity detection and barge-in. We give the
voice agent a *small* set of basic, low-context tools it can handle on its own
(reading tasks, answering a pending question) and one `ask_assistant` tool that
delegates anything heavier — research, Google, creating/scheduling tasks,
detailed work — to the **universal agent**, which owns all the tools and context.
The voice agent then speaks back whatever the assistant returns.

Audio I/O is event-based: the browser's mic frames arrive as `RecordedAudioEvent`
and Gemini's speech leaves as `SynthesizedAudioEvent` on the same conversation
stream. Gemini Live is fixed at 16 kHz mono PCM in / 24 kHz mono PCM out.
"""

import os

from agclaw.config import Config

# Distinct from the chat model — Gemini exposes realtime under a "live" model.
VOICE_MODEL = "gemini-3.1-flash-live-preview"

# Basic tools the voice agent may run itself: quick, low-context reads/answers.
# Everything else is delegated to the universal agent via ask_assistant.
_BASIC_VOICE_TOOLS = {
    "list_tasks",
    "get_task",
    "list_open_questions",
    "answer_question",
}

VOICE_PROMPT = (
    "You are AGClaw's voice. You're speaking with the user out loud, so be warm, "
    "natural, and concise — a sentence or two at a time, no markdown, no long "
    "lists read aloud. You are the same assistant they use in the app, just by "
    "voice.\n\n"
    "You can do a few simple things yourself: check their tasks, look up a task's "
    "status, see pending questions, and relay an answer to one.\n\n"
    "For anything beyond that — researching something, the web, their Google "
    "calendar/drive/mail, creating or scheduling a task, or any multi-step or "
    "detailed work — call `ask_assistant` with a clear, complete request. The "
    "main assistant will do the heavy lifting and hand you back an answer; then "
    "summarise it for the user in a natural, spoken way.\n\n"
    "Always involve the user as much as needed for clarity. If a request is "
    "ambiguous, ask a short follow-up question out loud before acting or "
    "delegating. Never guess at something you could simply ask about."
)


def voice_realtime_config(config: Config, voice: str = "Puck"):
    """Build a Gemini Live RealtimeConfig using AGClaw's configured API key.

    The config builds a genai Client eagerly, so we pass one explicitly rather
    than relying on ambient env. `transcribe=True` gives us the user's speech as
    text so we can show it on screen.
    """
    from google.genai import Client

    from autogen.beta.live import gemini

    api_key = os.environ.get(config.llm.api_key_env, "")
    client = Client(api_key=api_key)
    return gemini.RealTimeConfig(
        VOICE_MODEL,
        output=gemini.AudioOutput(voice=voice, language_code="en-US"),
        input=gemini.InputConfig(transcribe=True),
        client=client,
    )


def build_voice_agent(config: Config, tasks, delegate, voice: str = "Puck"):
    """A LiveAgent with a basic tool subset + an ask_assistant delegate tool.

    `tasks` is the TaskService (for the basic read tools); `delegate` is an async
    `(request: str) -> str` that runs the universal agent and returns its reply.
    """
    from autogen.beta import tool
    from autogen.beta.live import LiveAgent

    from agclaw.system_tools import build_system_tools

    basic = [t for t in build_system_tools(tasks) if t.name in _BASIC_VOICE_TOOLS]

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

    return LiveAgent(
        name="voice",
        prompt=VOICE_PROMPT,
        config=voice_realtime_config(config, voice=voice),
        tools=[*basic, ask_assistant],
    )
