"""AGClaw human-in-the-loop — pluggable question/permission asking per surface."""

from agclaw.hitl.base import Asker, Question, build_hitl_hook
from agclaw.hitl.channel import PendingAsks
from agclaw.hitl.desktop import DesktopAsker, HitlServer

__all__ = [
    "Asker",
    "Question",
    "build_hitl_hook",
    "DesktopAsker",
    "HitlServer",
    "PendingAsks",
]
