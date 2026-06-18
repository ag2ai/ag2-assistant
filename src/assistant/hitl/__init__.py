"""AGClaw human-in-the-loop — pluggable question/permission asking per surface."""

from assistant.hitl.base import Asker, Question, build_hitl_hook
from assistant.hitl.channel import PendingAsks
from assistant.hitl.desktop import DesktopAsker, HitlServer, add_hitl_routes
from assistant.hitl.gateway import GatewayAsker
from assistant.hitl.inquiry import DurableAsker, Inquiry, InquiryStatus, InquiryStore

__all__ = [
    "Asker",
    "Question",
    "build_hitl_hook",
    "DesktopAsker",
    "HitlServer",
    "GatewayAsker",
    "PendingAsks",
    "add_hitl_routes",
    "Inquiry",
    "InquiryStatus",
    "InquiryStore",
    "DurableAsker",
]
