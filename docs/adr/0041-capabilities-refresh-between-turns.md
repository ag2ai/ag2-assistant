---
status: accepted
date: 2026-10-03
---

# Capabilities refresh between Turns

For Issue #121, resolve the offered tools, Skills, and Cards at the actual start of
each Turn, after waiting for earlier work in that Chat; configuration changes apply
to that next Turn, including in existing Chats. A running Turn keeps its previous
capabilities, including when further messages join it, so changing configuration
does not alter an execution halfway through. This avoids preserving historical
configuration for the lifetime of each Chat while giving changes a predictable
boundary; it amends the reload-driven refresh in ADRs 0016 and 0039.

Removing an MCP server stops offering its tools to new Turns immediately. An
existing connection remains available to Turns already holding it and closes after
the last such Turn completes, fails, or is stopped; removing configuration must not
interrupt an execution already using that connection.

Share a Docker environment between Chats and model selections within one Profile
when its image and network settings match. Changing those settings gives new Turns
a new environment; the previous one closes after its last holding Turn ends. A
model switch alone preserves the environment and its container state.

Keep one long-lived Agent per Profile and prepare each invocation with the current
model configuration, tools, fresh invocation plugins, and guidance. Configurable
capabilities belong to the invocation rather than constructor contributions, which
would otherwise retain old tools and catalogs. Reuse MCP connections, Docker
environments, ACP model sessions, and memory stores separately from these invocation
contributions; this uses AG2's invocation API instead of rebuilding the Agent for
each Turn or keeping one Agent per model.

Replace the profile's Agent when constructor-bound settings change, including the
KnowledgeConfig governing memory aggregation and history compaction and the model
configuration used by those strategies. The replacement reuses long-lived resources;
Turns already holding the previous Agent complete on it. Skills, Cards, tools, and
the main model configuration continue to refresh through invocation arguments.

Snapshot Skill availability and Card definitions at Turn start, but do not copy or
version entire Skill directories. Skill instructions, resources, and scripts use
their files as they exist when accessed: an edit can be observed within the current
Turn and a deletion can cause an ordinary read or execution error. Enable/Disable
and Suppression changes affect the next Turn rather than changing its held catalog.

The current implementation scope excludes the ACP Agent serving path: ACP listeners
and their executor integration are deferred to separate work and do not block this
change. This deferral concerns external clients driving Assistant over ACP; ACP
model configurations used by Assistant still retain and reuse their per-chat sessions.
The shared preparation of Turns must remain usable by a future ACP integration,
without depending on a new upstream ACP API now. An already-running ACP listener
does not gain the next-Turn refresh guarantee from this work.

The Gateway implementation follows this boundary. External ACP serving refresh
remains separate work; this scope does not satisfy that part of the original
Issue #121 acceptance.
