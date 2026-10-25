# Transient room context over ACP

An ACP client may attach bounded room reference material after the original user
request. The first text block carries `_meta["ag2/task"]` with `task_id`, `room_id`,
`access_tier`, `source_message_id`, and optional independently known `agent_id`.
A later text block carries `_meta["ag2/room_context"] = {binding, context}`.
The examples in `tests/fixtures/room-context-*.json` describe the proposed v1 packet.

The native ACP listener removes all marked blocks before creating the ordinary user
request. It checks exact binding consistency, known agent identity, policy-compatible
message selection, expiry, JSON-safe timestamps, valid Unicode, section limits and
serialized UTF-8 size (32 KiB context, 8 KiB binding). Unknown versions/fields and
malformed packets become an unavailable notice while the real request survives.
A prompt containing only marked data causes no model turn. The binding is a consistency
check, not authorization; the client and authoritative producer must enforce policy.

A typed, process-local prepared value carries accepted data into model middleware.
The reference is untrusted model input for this turn, revalidated before each model
call. It never becomes a persisted ModelRequest, task instruction, or global memory
instruction. Compaction budgets the reference alongside protected inputs but does
not include it in checkpoint sources, summaries, or the latest saved request.

A content-free nonconversational event marks a context-enabled conversation. Passive
profile aggregation skips that entire conversation, including model/tool output and
later turns that may derive from room reference data. Existing compaction retains
the marker as telemetry, and durable ACP replay reloads it on reconnect. Ordinary
unmarked conversations retain their existing profile behavior; explicit memory tools
continue to obey the assistant's existing permission and owner instructions.

The ACP client must use separate context-aware sessions when the effective room,
agent, membership, tier, visibility, policy revision or availability changes. This
feature does not establish strict isolation of all historical profile/session memory.
It also does not activate server-side room onboarding, add independent history reads,
or produce a greeting on joining. Validation uses in-process ACP and scripted models;
live producer delivery remains a separate integration prerequisite.
