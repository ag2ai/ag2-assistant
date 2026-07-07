// Maps the AG2 event/item vocabulary to the framework subsystem behind it, so the
// UI can show "where AG2 is used". The GUI is a projection of one AG2 Stream, so
// every wire event {type, data} is an AG2 primitive (or an app event riding the
// AG2 stream). `tail` is the last segment of the qualified type name.

export const tail = (s) => (s || '').split('.').pop()

const DOCS = 'https://docs.ag2.ai'

// Subsystems: the legend for the inspector + Powered-by page.
export const SUBSYSTEMS = {
  Model: { color: '#4c8bf5', blurb: 'AG2 Agent ↔ provider (the LLM calls)' },
  Tool: { color: '#1f9d55', blurb: 'AG2 tools (search, shell, code, fetch, filesystem, MCP…)' },
  Memory: { color: '#9b59b6', blurb: 'AG2 KnowledgeConfig — aggregation & compaction' },
  Subagent: { color: '#e67e22', blurb: 'AG2 subagents — run_task lifecycle' },
  HITL: { color: '#e0b400', blurb: 'AG2 human-in-the-loop (context.input / hitl_hook)' },
  Observer: { color: '#c2410c', blurb: 'AG2 observers — alerts & halts' },
  Voice: { color: '#16a3a3', blurb: 'AG2 LiveAgent — realtime voice' },
  A2UI: { color: '#d8552f', blurb: 'A2UI declarative generative UI surfaces' },
  Usage: { color: '#888', blurb: 'AG2 token usage accounting' },
  Stream: { color: '#888', blurb: 'AG2 Stream lifecycle' },
}

// tail(type) → subsystem. Native AG2 events.
const EVENT_SUB = {
  ModelRequest: 'Model', ModelResponse: 'Model', ModelMessage: 'Model', ModelMessageChunk: 'Model',
  ToolCallsEvent: 'Tool', ToolCallEvent: 'Tool', ToolResultEvent: 'Tool', ToolResultsEvent: 'Tool',
  GeminiToolCallEvent: 'Tool', OpenAIToolCallEvent: 'Tool', AnthropicToolCallEvent: 'Tool',
  AggregationStarted: 'Memory', AggregationCompleted: 'Memory', AggregationFailed: 'Memory',
  CompactionStarted: 'Memory', CompactionCompleted: 'Memory', CompactionFailed: 'Memory',
  TaskStarted: 'Subagent', TaskCompleted: 'Subagent', TaskFailed: 'Subagent', TaskCancelled: 'Subagent',
  HumanInputRequest: 'HITL', HumanMessage: 'HITL',
  ObserverAlert: 'Observer', HaltEvent: 'Observer',
  UsageEvent: 'Usage',
  RecordedAudioEvent: 'Voice', SynthesizedAudioEvent: 'Voice',
  TranscriptionChunkEvent: 'Voice', TranscriptionCompletedEvent: 'Voice',
  A2UIMessageEvent: 'A2UI', A2UIClientEvent: 'A2UI', A2UIValidationFailedEvent: 'A2UI',
}

// App-defined events (assistant.events.*) — our concepts, but they ride the AG2
// Stream as BaseEvent subclasses, so still "on AG2", just app-layer.
const APP_EVENT_SUB = {
  TaskCreated: 'Subagent', TaskScheduled: 'Subagent', DeliverableProduced: 'Subagent',
  InquiryRaised: 'HITL', InquiryAnswered: 'HITL', SubagentTrace: 'Subagent',
  FeedbackGiven: 'Memory', // 👍/👎 feeds the learned memory profile
  A2UISurface: 'A2UI',
}


// Describe a wire event for the inspector.
export function describe(type) {
  const t = tail(type)
  const native = (type || '').startsWith('ag2.')
  const sub = EVENT_SUB[t] || APP_EVENT_SUB[t] || 'Stream'
  const layer = native ? 'ag2' : 'app' // app events still ride the AG2 stream
  return { sub, label: t, layer }
}

// Thread item kind → the AG2 primitive it's a projection of (for inline tags).
const ITEM_AG2 = {
  user: { sub: 'Model', label: 'ModelRequest', layer: 'ag2' },
  agent: { sub: 'Model', label: 'ModelResponse', layer: 'ag2' },
  tools: { sub: 'Tool', label: 'ToolCallsEvent → AG2 tools', layer: 'ag2' },
  subagent: { sub: 'Subagent', label: 'subagents.run_task', layer: 'ag2' },
  inquiry: { sub: 'HITL', label: 'HumanInputRequest / InquiryRaised', layer: 'ag2' },
  taskcard: { sub: 'Subagent', label: 'TaskCreated (app event on AG2 stream)', layer: 'app' },
  deliverable: { sub: 'Subagent', label: 'DeliverableProduced (app event)', layer: 'app' },
  a2ui: { sub: 'A2UI', label: 'A2UI surface', layer: 'app' },
  note: { sub: 'Stream', label: 'lifecycle note', layer: 'app' },
}
export const itemAg2 = (kind) => ITEM_AG2[kind] || null

// Curated architecture map for the "Powered by AG2" page. layer: 'ag2' (the
// framework gives you this) vs 'app' (built on top of AG2).
export const PRIMITIVES = [
  { sub: 'Model', name: 'ag2.Agent', what: 'The universal runtime: model config, tools, knowledge, assembly, HITL, middleware, observers', layer: 'ag2' },
  { sub: 'Stream', name: 'Stream + EventLogWriter', what: 'One event stream is the log, the wire protocol, and the source this whole UI is a projection of', layer: 'ag2' },
  { sub: 'Memory', name: 'KnowledgeConfig + WorkingMemoryAggregate', what: 'Persistent learned profile, distilled & injected each turn (+ SummarizeCompact)', layer: 'ag2' },
  { sub: 'Tool', name: 'Native tools', what: 'DuckDuckSearchTool, SandboxShell/CodeTool, WebFetchTool, FilesystemToolkit, SkillSearchToolkit, MCP', layer: 'ag2' },
  { sub: 'Subagent', name: 'subagents.run_task', what: 'Background task work runs as named AG2 subagents (visible, nested)', layer: 'ag2' },
  { sub: 'HITL', name: 'context.input / hitl_hook', what: 'Mid-turn questions & permission gates (durable inquiries ride the stream)', layer: 'ag2' },
  { sub: 'Voice', name: 'LiveAgent', what: 'Realtime voice (Gemini Live / OpenAI realtime), delegating heavy work to the universal agent', layer: 'ag2' },
  { sub: 'A2UI', name: 'autogen.beta.a2ui', what: 'Agent-authored declarative UI messages rendered by the chat/task surface', layer: 'ag2' },
  { sub: 'Observer', name: 'LoopDetector / TokenMonitor / custom', what: 'Stuck-turn guards emitting ObserverAlerts on the stream', layer: 'ag2' },
  { sub: 'Tool', name: 'extensions.docker.DockerEnvironment', what: 'Official AG2 container backend for the code/shell sandbox', layer: 'ag2' },
  { sub: 'Model', name: 'response_schema', what: 'Typed task planning & deliverable verification', layer: 'ag2' },
  { name: 'Durable task store + scheduler', what: 'Persistent tasks, recurrence, cascading cancel, deliverable verification', layer: 'app' },
  { name: 'Gateway + channels + Svelte UI', what: 'FastAPI/WebSocket facade, Telegram/Discord/Slack, this web client', layer: 'app' },
]

export const AG2_DOCS = DOCS
