"""Helpers shared by more than one route module (and by app.py's WebSocket
handlers, which are not routes and stay where they are).

A helper earns a place here only once a second caller appears in a different
module; one used by a single domain travels with that domain instead.
"""

from assistant.gateway.profile_manager import ProfileRuntime
from assistant.hitl import DurableAsker, GatewayAsker, NullAsker


def chat_asker(runtime: ProfileRuntime, chat_id: str):
    """Durable, inline HITL for a chat turn: the agent's question persists as an
    Inquiry and surfaces inline on this chat's stream (InquiryRaised),
    answerable from the thread or the strip. Falls back to the transient HITL
    registry if the inquiry store isn't available."""
    inquiries = runtime.tasks.inquiries if runtime.tasks is not None else None
    if inquiries is None:
        return GatewayAsker(runtime.hitl)
    return DurableAsker(NullAsker(), inquiries, chat=chat_id)
