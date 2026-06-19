"""AG2 Assistant gateway — a thin REST/WebSocket facade over an AG2 Hub.

The gateway boots an AG2 `Hub`, registers the AG2 Assistant agent on it, and exposes a
plain HTTP + WebSocket API so any UI client (web, desktop, mobile, CLI) can drive
the agent without speaking the AG2 network protocol. Optionally it also serves the
Hub over WebSocket (`serve_ws`) for native/distributed AG2 clients.
"""

from assistant.gateway.core import Gateway

__all__ = ["Gateway"]
