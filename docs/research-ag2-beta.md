# AG2 Beta Research

## Overview

AG2 Beta (`autogen.beta`) is AG2's next-generation framework that will become the official API at v1.0. It replaces the dict-based, synchronous stable API with a fully event-driven, streaming architecture.

- **Language:** Python
- **GitHub:** https://github.com/ag2ai/ag2
- **Path:** `autogen/beta/`
- **Version checked:** `ag2 0.13.4` (June 2026)

## Availability Summary

| Feature | On Main | Notes |
|---------|:-------:|-------|
| Agent + AgentReply | ✅ | With integrated KnowledgeConfig and TaskConfig |
| @tool + built-in tools | ✅ | Expanded built-in tool library |
| Middleware pipeline | ✅ | Logging, retry, token limiter, history limiter, telemetry, approval |
| Event/Stream system | ✅ | MemoryStream, event types, conditions |
| Response Schema | ✅ | Structured outputs with validation/retry |
| HITL hooks | ✅ | |
| LLM configs (5 providers) | ✅ | OpenAI, Anthropic, Gemini, Ollama, DashScope |
| Observer protocol + built-ins | ✅ | LoopDetector, TokenMonitor |
| Conversable adapter | ✅ | |
| **Assembly Policies** | ✅ | conversation, working_memory, episodic_memory, sliding_window, token_budget, alert |
| **KnowledgeStore** | ✅ | Memory, Disk, Locked, Redis, SQLite backends |
| **Compaction / Aggregation** | ✅ | Built-in strategies + middleware |
| **Watch system** | ✅ | EventWatch, CronWatch, IntervalWatch, etc. |
| **Subagents** | ✅ | Spawn sub-conversations as tools |
| **Skills runtime** | ✅ | Local skills, runtime, skill_search |
| **Docker extension** | ✅ | Containerized tool execution |
| **Daytona extension** | ✅ | Daytona sandbox integration |
| **AG-UI** | ✅ | UI protocol integration |
| **AgentSpec** | ✅ | Declarative agent definitions |
| **FilesAPI** | ✅ | Agent file output management |
| Actor class | ➖ | No longer needed — Agent now subsumes Actor functionality directly |
| **Network / Hub** | ✅ | **NEW in 0.13** — full multi-agent network: Hub, channels, governance, views |
| **Distributed transport** | ✅ | **NEW in 0.13** — WebSocket transport (`serve_ws`, `WsLink`) for cross-machine hubs; `RemoteAgentProxy` |

## Core API

### Agent

```python
from autogen.beta import Agent, KnowledgeConfig, TaskConfig
```

Agent now has direct integration for:
- `knowledge: KnowledgeConfig` — persistent knowledge store with compaction/aggregation
- `tasks: TaskConfig` — subtask spawning
- `observers: list[Observer]` — behavior monitoring
- `assembly: list[Policy]` — context assembly policies
- `compact: CompactStrategy + CompactTrigger` — automatic history reduction
- `aggregate: AggregateStrategy + AggregateTrigger` — knowledge extraction

The `Actor` class is no longer a separate concept — Agent handles it all.

### AgentReply

```python
reply = await agent.ask(message)
reply.body      # Text response
reply.files     # Generated files
reply.history   # Conversation history
reply.context   # Execution context
reply.response  # Raw model response
await reply.content(retries=0)  # Validated structured output
await reply.ask(continuation)   # Multi-turn continuation
```

### Tools

```python
from autogen.beta import tool

@tool
def my_tool(x: int) -> str:
    """Tool description."""
    return str(x)
```

**Built-in tools:**
- `CodeExecutionTool` (Gemini native + Docker/Daytona for others)
- `ShellTool` (with container environments via Docker/Daytona)
- `WebSearchTool` (provider-native: Gemini GoogleSearch, OpenAI web search, Anthropic web search)
- `WebFetchTool` (Gemini UrlContext, others provider-native)
- `ImageGenerationTool`
- `MemoryTool` (knowledge store operations)
- `SkillsTool` + `LocalSkillsTool` (skill discovery and execution)
- `SubagentTool` (spawn sub-agent conversations)
- `MCPServerTool` (schema-only stub — full MCP still in progress)

### Knowledge Store

```python
from autogen.beta.knowledge import (
    MemoryKnowledgeStore,
    DiskKnowledgeStore,
    SQLiteKnowledgeStore,
    RedisKnowledgeStore,
    LockedKnowledgeStore,
)

store = DiskKnowledgeStore("/path/to/knowledge")
# Or for persistence with SQL:
store = SQLiteKnowledgeStore("agent.db")

agent = Agent(
    name="my-agent",
    prompt="...",
    config=llm_config,
    knowledge=KnowledgeConfig(store=store),
)
```

### Assembly Policies

```python
from autogen.beta.policies import (
    ConversationPolicy,
    WorkingMemoryPolicy,
    EpisodicMemoryPolicy,
    SlidingWindowPolicy,
    TokenBudgetPolicy,
    AlertPolicy,
)

agent = Agent(
    ...,
    assembly=[
        ConversationPolicy(),
        WorkingMemoryPolicy(),
        SlidingWindowPolicy(max_events=50),
    ],
)
```

### Compaction & Aggregation

```python
from autogen.beta.compact import TailWindowCompact, SummarizeCompact
from autogen.beta.aggregate import ConversationSummaryAggregate

agent = Agent(
    ...,
    compact=TailWindowCompact(target=100),
    aggregate=ConversationSummaryAggregate(),
)
```

### Watch System

```python
from autogen.beta.watch import EventWatch, CronWatch, IntervalWatch

# Fire a callback when matching events occur
watch = EventWatch(condition, callback)

# Run periodically
watch = IntervalWatch(seconds=60, callback=poll_task)

# Cron schedule
watch = CronWatch(cron_expr="0 9 * * *", callback=daily_briefing)
```

### Observers

```python
from autogen.beta.observer import LoopDetector, TokenMonitor

agent = Agent(
    ...,
    observers=[
        LoopDetector(),
        TokenMonitor(budget=100000),
    ],
)
```

### Extensions

```python
# Docker sandbox for code/shell tools
from autogen.beta.extensions.docker import DockerEnvironment

env = DockerEnvironment(image="python:3.12")
shell_tool = ShellTool(environment=env)

# Daytona cloud sandbox
from autogen.beta.extensions.daytona import DaytonaEnvironment
```

### LLM Providers

OpenAI (+ Responses API), Anthropic, Gemini, Ollama, DashScope — each with dedicated client and mapper modules under `config/`.

## Network & Distributed (NEW in 0.13)

`autogen.beta.network` is a full multi-agent network stack. Two or more *distinct, registered* agents collaborate through a shared **Hub** over durable channels. (For one agent recursively spawning its own subtasks, use `TaskConfig` / subagents instead — that's not a network.)

### Hub

```python
from autogen.beta.network import Hub

async with await Hub.open() as hub:
    # register agents, adapters, listeners, arbiters, remote proxies
    ...
```

Hub responsibilities: agent registry, channel lifecycle, envelope routing, governance (rules/expectations/arbiter), audit log, listeners, sweepers, WAL replay.

### Channel adapters (interaction patterns)

| Adapter | Type const | Pattern |
|---------|-----------|---------|
| `ConsultingAdapter` | `consulting` | 2-party strict one-question-one-response |
| `ConversationAdapter` | `conversation` | 2-party free-form back-and-forth |
| `DiscussionAdapter` | `discussion` | N-party round-robin (`ORDERING_ROUND_ROBIN`) |
| `WorkflowAdapter` | `workflow` | Declarative orchestration via `TransitionGraph` (GroupChat successor) |

### Distributed transport

| Transport | Use |
|-----------|-----|
| `LocalLink` / `LocalLinkClient` | In-process agents, same event loop |
| `WsLink` / `serve_ws` / `WsLinkClient` | **Cross-machine** — Hub served over WebSocket, agents connect from other processes/hosts |
| `RemoteAgentProxy` | Hub-side proxy representing an agent reachable over a link |

Frame protocol (`transport/frames.py`): Hello/Welcome handshake, Request/Response, Notify, Ping/Pong, Receipt, Error.

### Governance

- `Rule` with `AccessBlock` / `LimitsBlock` / `RateBlock` / `InboxBlock` / `AuthBlock`
- `HubArbiter` / `RuleBasedArbiter` — swappable access & routing seam (`Allow` / `Deny`)
- `AuthAdapter` / `AuthRegistry` (`ApiKeyAuth`, `NoAuth`)
- `Expectation`s with `acks_within` / `reply_within` / `max_silence` / `turn_within`; handlers `audit` / `warn` / `auto_close`
- Append-only `AuditLog` with `AUDIT_KIND_*` constants
- `Passport` / `Resume` identity; `Resume.observed` track record for capability-based peer ranking

### Agent-side client surface

- `AgentClient` — register, open channels, send/handle envelopes
- Six auto-injected LLM-facing tools: `say`, `delegate`, `peers`, `channels`, `tasks`, `context`
- `ViewPolicy` (`FullTranscript`, `WindowedSummary`) controls what each agent perceives
- `HumanClient` for human participants

### Relevance to AGClaw

This is a major unlock for **Phase 7 (multi-agent)** and reshapes options for the **gateway**:
- Multi-agent coordination is now native — no custom orchestration needed
- WebSocket distributed transport could underpin AGClaw's own gateway or let AGClaw agents federate across machines
- Governance (rate limits, auth, audit) maps directly to OpenClaw's per-channel policies and approvals

See sibling skills: `ag2-network-quickstart`, `ag2-network-workflow`, `ag2-network-discussion`, `ag2-network-governance`, `ag2-network-tools-and-views`.

## Still Partial

- **MCP** — `MCPServerTool` is still schema-only. Full client implementation still in progress per the roadmap.

## Comparison: Stable vs Beta (main branch, June 2026)

| Aspect | Stable | Beta (main) |
|--------|--------|-------------|
| Agent Model | Single-turn, explicit loops | Streaming, stateful, async ask() with knowledge/tasks |
| Messages | Dict-based | Event-based, strongly typed |
| Memory | Conversation history only | KnowledgeStore (5 backends) + assembly policies |
| Middleware | Limited | Full hook pipeline (6 built-ins) |
| Tools | Function signatures | Protocol-based with schema inference + subagents/skills |
| Responses | Dict/string | Type-safe with validation & retry + FilesAPI |
| Observability | External logging | Event stream + observer protocol + LoopDetector/TokenMonitor |
| Sandboxing | None | Docker + Daytona extensions |
| Reactive triggers | None | Watch system (event/cron/interval) |
| Providers | OpenAI-centric | Multi-provider (5 providers) |
