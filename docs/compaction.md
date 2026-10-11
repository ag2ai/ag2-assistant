# Conversation compaction

Assistant checks the estimated full input before every conversation model call,
including calls after tools. The per-profile **History compaction threshold** in Advanced Settings
defaults to 50,000 estimated tokens and applies to the next Turn. It includes the
assembled instructions, memory, resolved tool schemas, response schema, Skill text,
and conversational events. Non-text inputs use AG2's modality estimates. This is
an input estimate, not an exact provider tokenizer; choose a budget that leaves
room for the model's response. Assistant does not infer model limits from names.
Compaction remains enabled when passive Profile memory is disabled.

One coordinator runs compaction through public AG2 extension points.
KnowledgeConfig carries the custom strategy and storage; the resolved model
client checks each request, and outer Turn middleware materializes its result
after successful completion. AG2's independent automatic trigger is disabled.
Profile aggregation runs against the original Turn history before materialization.

When an input exceeds the budget, Assistant first reuses exact loaded Skill text
and replaces large text tool results with bounded excerpts. The
read_compaction_source tool reads pages of the archived original, scoped to the
current Chat. Images and other binary outputs remain intact. The latest user
request remains intact.

If that is insufficient, a replayable old prefix becomes a versioned checkpoint:
goal, constraints, decisions, completed work, artifacts, pending work, unknown
external action outcomes, next action, needed Skills and resource references.
Decisions, completed work and artifacts carry validated source-event references.
The current request accompanies summary input, so corrections can update the
task state. An earlier free-text summary remains source context and is adopted
into the structured checkpoint on the next successful compaction.

Full instructions for needed Skills are carried verbatim in the model projection,
separately from the checkpoint. The checkpoint never replaces their procedures.
Older reads of those instructions become references to the current full text.
Inactive Skill bodies can leave model context; resource references allow selective
reloading. On a subsequent Turn, retained instructions are read through the
currently held runtime, respecting Enable/Disable and profile Suppression. A
checkpoint cannot make an unavailable Skill available.

The initial retention policy reserves half the remaining input budget for growth.
The checkpoint is limited to one quarter of the remaining budget; the recent tail
uses the other quarter, capped at 60 conversational events and snapped to AG2's
replay boundaries. The latest request is retained even when its earlier tool rounds
are summarized. If causal dependencies or protected content cannot fit, Assistant
reports an error before calling the provider rather than silently dropping events.

Summary input is processed in bounded fragments with a separate client from the
current Turn's resolved model. It receives no tools and cannot recursively enter
the budget wrapper. Schema, size, Skill identifiers, resource references and
evidence references are checked locally. Limits can be configured under memory
in the profile's config.yaml:

~~~yaml
memory:
  compact_max_tokens: 50000
  compact_max_calls: 8
  compact_timeout_s: 60
~~~

The call limit and timeout apply to one compaction attempt. Malformed output,
provider failure, timeout, cancellation and exhausted call budgets fail the
attempt while retaining source history. CompactionStarted, CompactionCompleted,
CompactionFailed and UsageEvent(kind="compaction") report lifecycle and cost;
the model-call count remains accurate when a provider omits usage.

Source snapshots and the versioned candidate are saved before writing the active
checkpoint pointer. During a Turn only the model projection is shortened; raw
stream history remains intact. On successful completion the checkpoint and recent
tail become current history. Gateway persistence on failure and cancellation is
unchanged. After a restart, a current log that predates materialization is
reconciled with the committed source snapshot before using the checkpoint.

The event archive retains original messages, tool results and Cards for complete
UI replay. Chat deletion removes both archive segments and checkpoint data.
Checkpoints are isolated by Chat, including Chats sharing one Profile Agent.
Recovery restores saved context; it does not restart an unfinished execution or
automatically repeat external actions with unknown outcomes.
