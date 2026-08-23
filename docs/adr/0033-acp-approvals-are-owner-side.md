# ACP approvals are owner-side, through the standard permission seam

When an ACP-driven turn hits a gated tool, the approval goes to the assistant's **owner** on the
owner's own surfaces — never to the ACP client. A remote party (a Space user, an editor session)
can ask for anything; only the person who owns the assistant can allow it. Over stdio the two are
the same OS user answering in a second window; over the network the distinction is the security
model.

This works with zero upstream changes because of a load-bearing asymmetry in ag2: `ACPAgent`
welds `hitl_hook=_reject_human_input` (so `context.input()` fails every ACP turn), but the
assistant's `PermissionManager` calls its `Asker` **directly** — never through `hitl_hook` — so
the standard approval flow runs untouched inside a served turn. Free-text questions stay cleanly
broken until ag2's injectable-hook seam ships (ag2ai/ag2#3177); approvals do not wait for it.

The wiring is an agent-level middleware that fills `context.dependencies[PermissionManager]`
**with `setdefault`, never assignment**: a Gateway `send_message` turn injects its own per-turn
manager (bound to that request's asker and chat) via `agent.run(dependencies=...)`, which
populates the context *before* middleware runs — so on the shared runtime agents that
manager-booted listeners reuse, web-UI turns keep their manager and only ACP turns fall through
to ours. Installation is idempotent so listener restarts cannot stack duplicates.

Fail posture: **silence is never consent.** An approval wait interrupted by `session/cancel` or a
dropped connection resolves as deny — the tool call fails with "approval not obtained", the side
effect never runs, and the session stays usable. A turn with no manager available at all also
fails closed ("no approver available"). Native in-client approvals
(`session/request_permission` rendered in Zed/Space) are a later enhancement on the upstream
seam, not part of this decision.
