# ag2.acp Agent-side changes for 1.0.3

**Audience:** AG2 OSS developer picking up the ACP Agent-side work
**Repo:** `ag2ai/ag2` — `ag2/acp/` (the **Agent/server** role from #3139; not the Client side)
**Baseline:** all file/line references verified against `main` at ag2 **1.0.2** (2026-08-15)
**Requested by:** AG2 Assistant (`ag2ai/ag2-assistant`), which serves an `Agent` over ACP and is
blocked from doing these things cleanly today. Both changes are small, additive, and backward
compatible.

---

## 0. ✅ RESOLVED on main — the acp 0.12.1 breakage

**Fixed, and better than requested.** Rather than pinning back, `main` adapted: `ag2/acp/dispatch.py`
no longer imports `DefaultMessageDispatcher` (it is rewritten around a local `InOrderUpdates`
class), and the floor was raised to `agent-client-protocol>=0.12.1,<0.13`. Nothing further needed —
this section is kept only as the record of what was broken in the released 1.0.2.

Consumers still pinned to `ag2==1.0.2` must hold `agent-client-protocol<0.12.1` until they move
to the next ag2 release, which requires `>=0.12.1`. The two cannot be satisfied together.

<details><summary>Original report</summary>

### The original problem (released 1.0.2)

**ag2 1.0.2 is broken with `agent-client-protocol` 0.12.1.** The pin is
`>=0.12.0,<0.13` (`pyproject.toml:76`), but 0.12.1 removed `DefaultMessageDispatcher`, which
`ag2/acp/dispatch.py:32` imports — so every fresh `pip install ag2[acp]` resolves 0.12.1 and gets
`ImportError` at `ag2.acp.dispatch`. Verified against both published wheels (present in 0.12.0,
absent in 0.12.1).

Fix in a patch release, whichever is quicker:

- tighten the pin to `>=0.12.0,<0.12.1`, **or**
- adapt `dispatch.py` to the 0.12.1 API.

(AG2 Assistant is guarding locally with `agent-client-protocol>=0.12.0,<0.12.1` meanwhile.)

</details>

---

## 1. Injectable `hitl_hook` on `ACPAgent`

> **STATUS: implemented in ag2ai/ag2#3177** (open, author marklysze, 2026-08-17) — `ACPAgent(...,
> hitl_hook=...)` passed through `AgentExecutor`, +254/−11 across `agent.py`, `executor.py`,
> `test/acp/test_agent_hitl.py`, `website/docs/user-guide/acp/server.mdx`. Matches this request.
> One behaviour to note for hosts: the **served agent's own `hitl_hook` stays overridden** with or
> without the argument, so a host must pass its hook to `ACPAgent` explicitly — setting it on the
> wrapped `Agent` alone has no effect. Section 2 below is **not** in that PR and remains open.

### Today

`AgentExecutor._dispatch` hard-wires human-input rejection — `executor.py:283`:

```python
hitl_hook=_reject_human_input,
```

`_reject_human_input` (`executor.py:398`) raises `HumanInputUnsupportedError`, failing the turn
on any `context.input()`. `ACPAgent.__init__` constructs the executor internally, so a hosting
application has **no way to override this** — an assistant with its own human-in-the-loop
machinery cannot ask its user anything during an ACP-driven turn.

### Requested change

A constructor parameter, passed through to the executor:

```python
ACPAgent(
    agent, *,
    ...,
    hitl_hook: Callable[[BaseEvent, Any], Awaitable[str]] | None = None,
)
```

- `None` (default) → exactly today's behavior (`_reject_human_input`). No existing user changes.
- Provided → the executor calls it wherever it calls `_reject_human_input` today; its return
  value is the human's answer, delivered as `context.input()`'s result.

The signature above matches `_reject_human_input`'s own
(`async def _reject_human_input(event: BaseEvent, context: Any) -> str`); reuse whatever hook
type the executor already expects rather than inventing a new one.

### Acceptance

- A turn whose agent calls `context.input()` completes with the injected hook's return value.
- With no hook supplied, the same turn still raises `HumanInputUnsupportedError` (existing tests
  keep passing unchanged).
- The hook is per-`ACPAgent`, so a host can bind it to the session's context (the factory
  closure over `bind` already provides per-connection scope).

### Explicitly out of scope here

Advertising/implementing ACP `session/request_permission` or elicitation on the server side.
That is a later, larger change; this parameter is deliberately protocol-invisible.

---

## 2. Credential-state auth probe (fixes the terminal-auth deadlock)

### Today

`ACPAgent` tracks *"has the ACP `authenticate` method been called on this connection"* when the
real question is *"do credentials exist"*:

- `agent.py:244` — `self._authenticated = owner._auth is None` (a connection with a configured
  `AuthProvider` starts unauthenticated)
- `agent.py:315` — the **only** place it becomes `True`: the client calling `authenticate`
- `agent.py:480-492` — `_require_session_scope()` refuses `session/new` etc. with
  `auth_required` while `False`

That is correct for **Agent Auth** (the flow runs over the wire). It deadlocks **Terminal
Auth**, which completes out of band *by design* — the client spawns the agent's setup command in
a terminal, credentials land on disk, and nothing in the protocol ever flips the flag. A
terminal-only provider leaves every session permanently `auth_required` while valid credentials
sit on disk. (For reference: 12 of the 29 working agents in the ACP Registry declare `terminal`,
including OpenCode and Kilo — this is a mainstream method, not an edge case.)

### Requested change

Gate on credential state, not call history. Add an **optional** probe to the `AuthProvider`
protocol:

```python
class AuthProvider(Protocol):
    ...
    async def is_authenticated(self) -> bool: ...   # optional; absent = today's behavior
```

And in `ACPAgent`:

- consult it when seeding `_authenticated` at connection start (`agent.py:244`), and
- **re-check it before rejecting** a gated request (`_require_session_scope`, or at each
  `session/new`) — so out-of-band setup is picked up on the next request with no restart and no
  wire call.

Providers without the method keep exactly today's semantics.

### Two riders that belong in the same change

1. **Advertise the terminal command.** A `terminal`-type auth method is only actionable if the
   client knows what to spawn. The advertised auth-method entry should carry the command spec
   per the auth-methods RFD / registry conventions.
2. **Give `methods()` sight of client capabilities.** `AuthProvider.methods()` takes no
   arguments today, so it cannot vary on what the client supports. Add an optional parameter
   (e.g. `methods(client_capabilities=None)`), noting the registry's CI signals terminal support
   via `clientCapabilities._meta["terminal-auth"]` — not the spec's `clientCapabilities.auth.terminal`
   — so any capability gate must accept both spellings.

### Acceptance

- A terminal-only provider whose `is_authenticated()` returns `True` serves `session/new`
  without any `authenticate` call.
- One that returns `False` is refused with `auth_required`, and a subsequent request **after the
  probe starts returning `True`** succeeds on the same connection — no reconnect required.
- Agent-Auth providers and probe-less providers behave exactly as in 1.0.2.

---

## Not in 1.0.3 (planned separately, listed so nothing gets pulled in by accident)

- Server-side `session/request_permission` / elicitation capability (in-client approvals).
- `session/load` and session continuity — see the separate proposal
  `acp-session-continuity-in-ag2-oss.md`, currently under evaluation.
- Remote transport serving (accept loop / HTTP / WebSocket) in `ACPAgent` — the hosting
  application drives the SDK's own server against `ACPAgent.bind`; no ag2 change requested.

## Verifying every claim

- Welded hook: `ag2/acp/executor.py:283`, `:398` on `main`.
- Auth mechanics: `ag2/acp/agent.py:244`, `:315`, `:480-492` on `main`.
- 0.12.1 breakage: `ag2/acp/dispatch.py:32` vs the `acp/task/__init__.py` in the 0.12.0 and
  0.12.1 wheels on PyPI.
- Registry terminal-auth adoption: `.protocol-matrix/latest.json` in
  `agentclientprotocol/registry` (`agents[].authMethods`).
