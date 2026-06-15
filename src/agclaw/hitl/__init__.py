"""AGClaw human-in-the-loop — pluggable question/permission asking per surface."""

from agclaw.hitl.base import Asker, Question, build_hitl_hook
from agclaw.hitl.channel import PendingAsks
from agclaw.hitl.desktop import DesktopAsker, HitlServer, add_hitl_routes
from agclaw.hitl.gateway import GatewayAsker
from agclaw.hitl.inquiry import DurableAsker, Inquiry, InquiryStatus, InquiryStore

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
