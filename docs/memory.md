# AGClaw Observer Memory

AGClaw passively learns about you over time and remembers it across conversations and platforms. This is the "personal" in personal assistant — the agent adapts to how you work without you having to repeat yourself.

## What it learns

The profile is organised under four dimensions:

| Dimension | Examples |
|-----------|----------|
| **How they like things done** | "Prefers bullet points over paragraphs", "Wants concise answers" |
| **When they like things done** | "Checks messages around 7am", "Prefers deep work in mornings" |
| **What they dislike** | "No emojis in work messages", "Dislikes long preambles" |
| **How they write** | "Warm but brief tone for emails", "Signs off with first name" |

Each observation is tagged with the platform it was seen on (cli, telegram, discord, …) so a single global profile still carries channel context — e.g. "formal on Slack (on slack)".

## How it works

AGClaw uses AG2 Beta's native knowledge and memory primitives — nothing bespoke:

```
Conversation ends
      │
      ▼
WorkingMemoryAggregate   ← custom prompt distills durable preferences
      │                    (4 dimensions, platform-tagged)
      ▼
SqliteKnowledgeStore     ← persists to ~/.agclaw/profile.db (/memory/working.md)
      │
      ▼  (next conversation, any process/platform)
WorkingMemoryPolicy      ← injects the profile into the agent's context
      │
      ▼
Agent acts on what it knows about you
```

- **Passive**: learning happens automatically after each conversation via an aggregation LLM pass. You don't have to tell it to remember.
- **Persistent**: stored in SQLite at `~/.agclaw/profile.db`, survives restarts.
- **Global + platform-aware**: one profile shared across all channels, with platform noted per observation.
- **Private to the model's behaviour**: the profile shapes responses via context injection. The knowledge tool and event-log dumping are turned off (`expose_tool=False`, `write_event_log=False`) to keep it purely observational.

## Implementation

| Concern | AG2 primitive | AGClaw code |
|---------|---------------|-------------|
| Storage | `SqliteKnowledgeStore` | `memory.build_knowledge_config()` |
| Learning | `WorkingMemoryAggregate(prompt=…)` + `AggregateTrigger(on_end=True)` | `memory.build_profile_prompt()` |
| Recall | `WorkingMemoryPolicy` + `ConversationPolicy` | `memory.profile_assembly()` |
| Wiring | `KnowledgeConfig` + `Agent(knowledge=, assembly=)` | `agent.create_agent(memory=True, platform=…)` |

See `src/agclaw/memory.py`.

## CLI

```bash
# Talk normally — learning happens passively
agclaw agent "I prefer short bulleted answers and I hate emojis at work"

# See what AGClaw has learned about you
agclaw profile show

# Start fresh
agclaw profile clear

# Disable memory for a one-off
agclaw agent "..." --no-memory

# Tag the platform (channels set this automatically)
agclaw agent "..." --platform telegram
```

## Design notes

- **Aggregation cadence**: currently fires after every interaction (`on_end=True`). Each fire is one cheap LLM call. For high-volume use we may switch to `every_n_turns=N` to reduce cost.
- **Custom prompt over default**: AG2's default `WorkingMemoryAggregate` prompt is content-oriented (tracks *what* was discussed). We override it with a preference-oriented prompt (tracks *how the user likes things*), which is the whole point of the observer.
- **Platform baked into the prompt**: because we build the agent per session with a known platform, the platform string is interpolated directly into the aggregation prompt — so observations are correctly attributed without needing it inside the event stream.
- **Future — per-platform nuance**: once channel adapters land (Phase 3), the platform flows from the adapter into `ask(platform=…)`. We may later split or sub-section the profile per platform if a single global profile proves too coarse.
