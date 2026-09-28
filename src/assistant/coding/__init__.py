"""Drive host CLI coding agents (Claude Code, Codex, OpenCode) over ACP.

AG2 Assistant plays the ACP *Client* role via ``ag2.acp``; each installed CLI
agent runs as an ACP *Agent* subprocess. This package handles host detection,
per-agent config, and the orchestration of one coding run (directory gating,
diff capture, and the streamed CodingSession surface).

The opposite role — AG2 Assistant itself served as an ACP Agent to an external
client — lives in ``src/assistant/acp/``.
"""
