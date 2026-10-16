---
status: accepted
date: 2026-10-11
---

# Budget full model input with conversation checkpoints

An after-Turn history threshold cannot prevent the first model call from
overflowing, or protect calls within a growing tool loop. A text summary can also
lose the task's constraints, unresolved external actions and loaded Skill rules.

Assistant owns compaction through AG2's public ModelConfig/client, middleware,
CompactStrategy, event stream and KnowledgeStore interfaces. One coordinator checks
assembled input and actual tool/response schemas before every call. The existing
per-profile token threshold becomes a full estimated input budget. The independent
AG2 automatic trigger is disabled; profile aggregation still runs before the raw
Turn history is materialized.

Replace covered history with a versioned, locally validated conversation checkpoint
and recent replayable events. Keep the latest user request and required Skill
instructions intact. Carry needed Skill/resource references and task results in the
checkpoint; preserve procedural text separately through the held Skill runtime.
Availability remains a Turn capability, never something a checkpoint can grant.

Persist source snapshots and the candidate before activating its pointer. During
the Turn only the model projection changes; the raw stream remains available for
failure/cancellation persistence. On restart reconcile an unmaterialized log with
the committed snapshot. Complete UI and Card replay continue to use the event
archive. Chat deletion removes checkpoint artifacts too.

The estimate is intentionally independent of provider tokenizers and guessed model
windows. Users choose a budget with response headroom. Bounded summary fragments,
call limits and deadlines limit maintenance work; protected-context overflow and
invalid summaries surface as errors before the main provider call. Recovery does
not automatically repeat tools or resume an interrupted execution.

See [the algorithm and configuration](../compaction.md).
