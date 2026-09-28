"""Serve AG2 Assistant's profile agents over the Agent Client Protocol (ACP).

AG2 Assistant plays the ACP **Agent** (server) role here: an external ACP
client — an editor, the ACP Registry, a remote seat — drives AG2 Assistant's own
agent. This is the opposite protocol end from ``src/assistant/coding/``, which
plays the ACP **Client** role: AG2 Assistant driving host CLI coding agents.
Adjacent names, opposite ends of the wire — do not confuse the two.
"""
