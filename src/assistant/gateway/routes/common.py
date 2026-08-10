"""Helpers shared by more than one route module (and by app.py's WebSocket
handlers, which are not routes and stay where they are).

A helper earns a place here only once a second caller appears in a different
module; one used by a single domain travels with that domain instead.
"""

import contextlib

from assistant.gateway.profile_manager import ProfileManager, ProfileRuntime
from assistant.hitl import DurableAsker, GatewayAsker, NullAsker


async def reload_all(manager: ProfileManager) -> None:
    """Reference-swap reload of every running runtime, so all profiles' agents pick
    up an install-wide change on their next turn.

    Anything that alters what a turn resolves — a saved key, a new active LLM
    config, a Google or ChatGPT sign-in — has to run this: a runtime holds its
    agent built from the config it booted with. Failures are suppressed per
    runtime because one wedged profile must not block the rest from reloading.
    """
    for runtime in list(manager.runtimes()):
        with contextlib.suppress(Exception):
            await manager.reload(runtime.pid)


async def scope_task_id(runtime: ProfileRuntime, chat_id: str) -> str:
    """The task whose Folder Grants the ``chat_id`` scope token names: ``task:{id}`` (an
    open Task page) directly, ``task-run:{run_id}`` (a run thread) via ``get_run``, else
    ``""`` (a real chat id or none) — ADR 0006/0013.

    Shared because the Thread scope is decoded identically wherever a Grant is
    resolved: by every ``/files/*`` route and by ``/folders/roots``, which are two
    domains and so two modules.
    """
    if chat_id.startswith("task:"):
        return chat_id.removeprefix("task:")
    if chat_id.startswith("task-run:"):
        with contextlib.suppress(Exception):
            run = await runtime.tasks.get_run(chat_id.removeprefix("task-run:"))
            return (run or {}).get("task_id") or ""
    return ""


def chat_asker(runtime: ProfileRuntime, chat_id: str):
    """Durable, inline HITL for a chat turn: the agent's question persists as an
    Inquiry and surfaces inline on this chat's stream (InquiryRaised),
    answerable from the thread or the strip. Falls back to the transient HITL
    registry if the inquiry store isn't available."""
    inquiries = runtime.tasks.inquiries if runtime.tasks is not None else None
    if inquiries is None:
        return GatewayAsker(runtime.hitl)
    return DurableAsker(NullAsker(), inquiries, chat=chat_id)
