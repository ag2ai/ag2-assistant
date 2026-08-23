# ACP session continuity in AG2 OSS

**Status:** Proposal for evaluation
**Audience:** AG2 OSS maintainers
**Repo it concerns:** `ag2ai/ag2` — specifically `ag2/acp/` (the ACP **Agent** side, added in #3139)
**Not** a request against AG2 Assistant, AG2 Space, or the ACP Client side.
**Date:** 2026-08-14

---

## Summary

`ag2.acp.ACPAgent` advertises `load_session=False` and implements none of ACP's session
continuity surface. A client that disconnects cannot get back to its conversation; there is no
protocol route into a session that already exists.

`load_session` is a **core ACP v1 capability**, not an optional extension — it is a top-level
field on `AgentCapabilities`, alongside `prompt_capabilities` and `mcp_capabilities`. The
optional session features (`list`, `delete`, `fork`, `resume`, `close`) live in a separate nested
`SessionCapabilities` block. AG2 currently declares neither the core capability nor any of the
nested ones.

Of the 29 agents in the ACP Registry that start successfully, **28 support `load_session`**. An
AG2 agent served over ACP would be the only one that does not.

This document sets out the gap, the evidence, and a scoped request. It does not assume the
request should be granted — the trade-offs are listed for evaluation.

## The gap

### What the code says

`ag2/acp/agent.py`, on `main`:

```python
286:    def _capabilities(self) -> schema.AgentCapabilities:
287:        return schema.AgentCapabilities(
289:            load_session=False,
```

`SessionCapabilities` is not populated, so `list`, `delete`, `fork`, `resume` and `close` are all
absent too.

Session state is dropped at three points in `ag2/acp/sessions.py`:

```python
541:  await self._storage.drop_history(session.stream_id)   # session close
557:  await self._storage.drop_history(session.stream_id)   # TTL expiry of an idle session
577:  await self._storage.drop_history(session.stream_id)   # eviction when max sessions reached
```

Session ids and stream ids are internally minted `uuid4()` values with no hook for an application
to supply or reconcile them.

### What that adds up to

A session exists only for the life of a connection. When it ends — deliberately, by idle timeout,
or by eviction under load — the conversation is unreachable. The client holds a session id that
the agent will no longer honour, and the protocol offers it no way to say "resume that one".

### One nuance in AG2's favour

`SessionConfig.storage` is injectable and takes an `ag2.history.Storage` — a four-method async
Protocol (`save_event`, `get_history`, `set_history`, `drop_history`). Because `drop_history` is
a call *into the application's implementation*, an application can already decline to destroy the
transcript and keep its own durable copy.

That solves persistence on the application's side. It does **not** solve continuity, because
there is no protocol path back in: with `load_session=False`, a client cannot ask the agent to
rehydrate a session even when the application still has every event. The data can survive; the
conversation cannot resume.

## Evidence that this is an outlier

Source: the ACP Registry's own CI probe matrix, `.protocol-matrix/latest.json` in
`agentclientprotocol/registry`, generated 2026-08-13. 31 agents probed, 29 initialized
successfully.

| Capability | Agents supporting it |
| --- | --- |
| `loadSession` | **28 / 29** |
| `sessionList` | 23 / 29 |
| `sessionResume` | 15 / 29 |

The 28 include every category of implementation — vendor-backed (`claude-acp`, `codex-acp`,
`cursor`, `gemini`, `github-copilot`, `devin`, `junie`, `qwen-code`), open-source and BYO-key
(`opencode`, `kilo`, `cline`, `goose`, `harn`, `sigit`, `stakpak`, `dirac`), and small
independents (`autohand`, `dimcode`, `nova`, `pi-acp`).

`load_session` is not a differentiator among ACP agents. It is the floor.

## Why this matters to AG2 OSS

The framing that matters for evaluation: **this is not an AG2 Assistant problem.** It affects any
AG2 `Agent` exposed over ACP, including anything served through `ag2 serve --protocol acp`. The
cost of the gap scales with how successful ACP hosting becomes.

Concrete consequences today:

1. **Long-lived remote clients cannot recover.** Any client that is not co-located — a hosted
   frontend, a chat bridge, a queue worker — will drop connections routinely. Every drop starts a
   new conversation with no memory of the last. This is the motivating case for AG2 Assistant, but
   it applies to every remote deployment.

2. **Editor clients lose work across restarts.** The dominant ACP client today is an editor. Users
   close editors, reload windows, and reboot. Every other agent in the registry survives that;
   an AG2 agent would not.

3. **TTL and eviction become silent data loss.** `sessions.py` evicts idle sessions and enforces a
   max — reasonable resource management, but without `load_session` an eviction is indistinguishable
   from a deletion from the client's point of view. Under load, an AG2 agent silently forgets
   conversations that are merely idle.

4. **The capability gap is publicly visible.** The registry publishes the probe matrix. Any AG2
   agent listed there shows `loadSession: false` next to 28 agents that do not.

5. **It blocks the session extensions.** `session/list`, `session/resume` and `session/fork` all
   presuppose that a session can be addressed after the fact. Without the core capability, none of
   the nested `SessionCapabilities` features are reachable either.

## Containerised deployment makes this sharper

The consequences above assume connections drop occasionally. In a container they do not drop
occasionally — they drop **on a schedule**, and the schedule is someone else's.

A containerised ACP-served agent is restarted by image updates, config changes, rolling deploys,
OOM kills, host reboots, and orchestrator rescheduling. Each of those is a routine, expected,
often automated event. Today every one of them destroys every session on that agent, and the
client learns about it only by finding that its session id no longer means anything.

This changes the character of the argument. "A network blip loses a conversation" is a robustness
complaint. "Every deploy loses every conversation on the instance" is a deployment-model
limitation — and it is the deployment model that remote ACP hosting implies, because a container
is how a remote agent is normally shipped.

Three specifics worth the implementer's attention:

1. **Containers are where remote transport lives, and remote clients reconnect most.** Local stdio
   is the transport that copes best without `session/load`, because the client owns the process
   lifetime and a lost process is visibly the client's own doing. The containerised remote case is
   the opposite: the agent's lifetime is controlled by infrastructure the client cannot see, so
   the client is the party that must recover — and today it cannot.

2. **A durable `Storage` is necessary but not sufficient.** `MemoryStorage` is the default, so a
   restart currently loses history even for an application that wanted to keep it. An application
   can supply a durable implementation — AG2 Assistant, for example, already runs with
   `AG2ASSISTANT_DATA_DIR=/data` and `VOLUME ["/data", "/workspace"]`, so it has somewhere durable
   to write. But durability on the application side is wasted while the protocol offers no way
   back into a session: the events survive the restart and remain unreachable. That asymmetry —
   application-side persistence already solved, protocol-side continuity missing — is the crux of
   this request.

3. **Memory pressure makes Tier 2 matter more, not less.** Containers run with memory limits, so
   the session manager's `_max` eviction and idle TTL are more likely to fire, not less. Under a
   tight limit, evicting idle sessions is exactly the right thing to do — but only if eviction is
   recoverable. Without `session/load`, a well-behaved resource-management decision is
   indistinguishable from data loss, and the tighter the limit the more often it happens.

Nothing here is specific to AG2 Assistant; it applies to any AG2 agent shipped as a container and
served over ACP, which is the expected shape for `ag2 serve --protocol acp` in production.

## What is being requested

Tiered, so the evaluation can stop at whatever line is justified.

**Tier 1 — the core capability.** Implement `session/load` and advertise
`load_session=True`. A client presents a session id it was given previously; the agent rehydrates
the session from storage and replays its history. This is the request. Everything below is
optional.

**Tier 2 — make eviction non-destructive.** Reconsider the three `drop_history` call sites.
Evicting a session from the in-memory map and destroying its transcript are different acts, and
`load_session` only helps if the second does not follow the first automatically. Consider
separating "release the live session" from "discard its history", so an application-supplied
`Storage` decides retention rather than inheriting the session manager's lifecycle.

**Tier 3 — the nested session capabilities.** `session/list` (23/29) and `session/resume` (15/29)
are meaningfully adopted and become straightforward once Tier 1 exists. `fork`, `delete` and
`close` are lower priority on current adoption.

An application-facing hook to influence or observe session id minting would help applications
reconcile ACP sessions with their own conversation records, but it is a convenience, not part of
the core request.

## What is explicitly not being asked

- No change to the ACP **Client** side (`ACPConfig`, `ClaudeCodeConfig`, the remote client work in
  #3146). This concerns the Agent side only.
- No remote transport work. That is a separate matter, and the Python SDK already ships
  `acp.http` / `acp.ws` in `agent-client-protocol` 0.12.
- No change to authentication, HITL, or permissions. Those are tracked separately.
- No new public API surface beyond what implementing a standard ACP capability requires.

## A security consequence the implementation must design for

This is not an argument against the request. It is a requirement that comes with it, and it is
easier to design for at the start than to retrofit.

**`load_session` turns a session id into a credential.**

Today a session id is an internally-minted `uuid4()` that dies with its connection. It grants
nothing, because there is no operation that accepts one after the fact. It is worth nothing to an
attacker.

With `session/load`, presenting a session id is a request to be given a conversation — its
transcript, and a live turn against whatever the bound agent can reach: its tools, its
filesystem access, its credentials. The id stops being an internal handle and becomes the thing
that gates access to all of it.

As far as we can determine from the ACP schema, **the protocol defines no binding between a
session id and an authenticated party.** Nothing in `session/load` requires the caller to be the
party the id was issued to. That is left to the agent, which means AG2 has to decide it rather
than inherit it.

Questions the implementation should answer deliberately:

- **Unguessability.** `uuid4()` is fine for an internal handle and is the right starting point for
  a bearer token, but the decision should be explicit rather than incidental — and it should stay
  explicit if id minting is ever made application-influenceable.
- **Scope.** Should a session be loadable by anyone who knows its id, or only within the same
  authenticated principal, the same connection identity, or the same transport session? An agent
  that restricts this is strictly safer, and the restriction is much harder to add later without
  breaking clients.
- **Expiry.** Should ids remain loadable indefinitely? Tier 2 makes eviction non-destructive,
  which means an id may stay valid long after the session left memory — a longer exposure window
  than exists today.
- **Transport asymmetry.** Over stdio the caller is the OS user who launched the process, and the
  id never leaves the machine. Over a remote transport the caller is a network peer and the id
  crosses the wire on every reconnect. The same capability carries materially different risk on
  the two transports, and the agent may reasonably treat them differently.
- **Containers concentrate the risk.** A containerised agent is reached over the network by
  definition, so every session id crosses a wire, and long-lived ids outlive the process that
  minted them. It is also the deployment where an agent is least likely to know who its caller is:
  AG2 Assistant's own `docker-compose.yml` warns that its existing gateway *"has no auth of its
  own. Publish 8800 only on a trusted network, or put it behind a reverse proxy."* An ACP listener
  deployed the same way inherits that posture, and a loadable session id deployed behind no
  authentication is a conversation anyone who can reach the port can claim.

None of this blocks Tier 1. It argues for deciding the scoping rule as part of the design rather
than defaulting to "any caller with the id", which is the outcome of not deciding.

## Trade-offs and counterarguments

Stated plainly, because the evaluation should weigh them:

- **It is real work.** Session rehydration means reconstructing an `Agent` turn context from
  stored events, and the current implementation mints its own ids and owns its own session
  lifecycle. This is not a flag flip.
- **`MemoryStorage` is the default**, so out of the box a "loaded" session would still be lost on
  process restart. `load_session=True` is only fully meaningful with a durable `Storage`, which
  raises the question of whether AG2 should ship one or continue to leave it to applications.
- **Correctness risk.** Replaying history into a live agent has to be exactly right, or a resumed
  session silently diverges from what the client believes it contains. Advertising the capability
  and implementing it subtly wrongly is worse than not advertising it.
- **Adoption is not obligation.** 28/29 is strong evidence of an expectation, not proof that AG2's
  use cases require it. If AG2's ACP hosting is expected to be short-lived and local, the gap
  matters much less.
- **Scope creep.** Tier 1 invites Tier 3, and `SessionCapabilities` has six fields.

## Open questions for the evaluator

1. Is `session/load` in scope for the ACP Agent implementation's stated goals, or was
   `load_session=False` a deliberate MVP boundary?
2. If deliberate, is there a recorded intent to revisit it, and on what timeline?
3. Should AG2 ship a durable `Storage` implementation, or remain application-supplied?
4. Should the three `drop_history` call sites be reconsidered independently of `session/load`?
   Tier 2 has value on its own, since it lets applications keep transcripts today.
5. Does anything in the current design make `session/load` harder than it appears — particularly
   the private `_ConnectionScope` structure and internally-minted ids?
6. What should scope a `session/load` call — any caller holding the id, or only the same
   authenticated principal / connection identity? Should stdio and remote transports differ? See
   the security section above; this is the one decision that is expensive to change after clients
   depend on it.

## How to verify every claim here

- `load_session=False` — `ag2/acp/agent.py:286-289` on `ag2ai/ag2@main`.
- The three drop points — `ag2/acp/sessions.py:541,557,577` on `main`.
- `Storage` shape — `ag2/history.py` on `main`, `class Storage(Protocol)`, four async methods.
- `load_session` is core, not an extension — `acp.schema.AgentCapabilities` has `load_session` as
  a top-level field; `list` / `delete` / `fork` / `resume` / `close` are fields of the separate
  `SessionCapabilities`. Verified against `agent-client-protocol` as installed.
- Registry adoption figures — `.protocol-matrix/latest.json` in `agentclientprotocol/registry`,
  `agents[].capabilities.loadSession`, dated 2026-08-13. Regenerate by re-reading that file; it is
  refreshed by the registry's own CI.
