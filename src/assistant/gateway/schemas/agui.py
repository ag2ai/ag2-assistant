"""Response bodies of the AG-UI routes that are plain JSON (the streams name none)."""

from pydantic import BaseModel


class FeedResponse(BaseModel):
    """POST /api/p/{pid}/chats/{chat_id}/feed — whether a running Turn took the message."""

    fed: bool
