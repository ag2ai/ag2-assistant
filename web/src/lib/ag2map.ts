// Maps the AG2 event/item vocabulary to the framework subsystem behind it, so the
// UI can show "where AG2 is used". The GUI is a projection of one AG2 Stream, so
// every wire event {type, data} is an AG2 primitive (or an app event riding the
// AG2 stream). `tail` is the last segment of the qualified type name.

import { m } from '../paraglide/messages.js'

export const tail = (s: string | null | undefined): string => (s || '').split('.').pop() ?? ''

const DOCS = 'https://docs.ag2.ai'

// Subsystems: the legend for the inspector + Powered-by page.
// The inspector legend: subsystem → its colour and one-line blurb. The KEY is the
// subsystem's AG2 name (matched by string equality all over this module and rendered
// as the legend's own label), so it stays as it is; `blurb` is prose, so it is a
// message function read at render time.
export type Subsystem = { color: string; blurb: () => string }

export const SUBSYSTEMS: Record<string, Subsystem> = {
  Model: { color: '#4c8bf5', blurb: m.ag2_sub_model },
  Tool: { color: '#1f9d55', blurb: m.ag2_sub_tool },
  Memory: { color: '#9b59b6', blurb: m.ag2_sub_memory },
  Subagent: { color: '#e67e22', blurb: m.ag2_sub_subagent },
  HITL: { color: '#e0b400', blurb: m.ag2_sub_hitl },
  Observer: { color: '#c2410c', blurb: m.ag2_sub_observer },
  Voice: { color: '#16a3a3', blurb: m.ag2_sub_voice },
  A2UI: { color: '#d8552f', blurb: m.ag2_sub_a2ui },
  Usage: { color: '#888', blurb: m.ag2_sub_usage },
  Stream: { color: '#888', blurb: m.ag2_sub_stream },
}

// tail(type) → subsystem. Native AG2 events.
const EVENT_SUB: Record<string, string> = {
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
const APP_EVENT_SUB: Record<string, string> = {
  TaskCreated: 'Subagent', TaskScheduled: 'Subagent', DeliverableProduced: 'Subagent',
  InquiryRaised: 'HITL', InquiryAnswered: 'HITL', SubagentTrace: 'Subagent',
  FeedbackGiven: 'Memory', // 👍/👎 feeds the learned memory profile
  FeedbackCleared: 'Memory', // rating retracted (thumb toggled off; no memory change)
  A2UISurface: 'A2UI',
}


// What subsystem an event or item belongs to, and whether it is AG2's or ours.
// `label` is a message function because a few of the item tags below are prose
// rather than an AG2 class name; the ones that ARE class names return themselves
// verbatim, since a wire type is not translated.
export type Ag2Tag = { sub: string; label: () => string; layer: 'ag2' | 'app' }

// Describe a wire event for the inspector.
export function describe(type: string | null | undefined): Ag2Tag {
  const t = tail(type)
  const native = (type || '').startsWith('ag2.')
  const sub = EVENT_SUB[t] || APP_EVENT_SUB[t] || 'Stream'
  const layer = native ? 'ag2' : 'app' // app events still ride the AG2 stream
  return { sub, label: () => t, layer }   // t is the wire type name — never translated
}

// Thread item kind → the AG2 primitive it's a projection of (for inline tags).
const ITEM_AG2: Record<string, Ag2Tag> = {
  user: { sub: 'Model', label: () => 'ModelRequest', layer: 'ag2' },
  agent: { sub: 'Model', label: () => 'ModelResponse', layer: 'ag2' },
  tools: { sub: 'Tool', label: m.ag2_item_tools, layer: 'ag2' },
  subagent: { sub: 'Subagent', label: () => 'subagents.run_task', layer: 'ag2' },
  inquiry: { sub: 'HITL', label: () => 'HumanInputRequest / InquiryRaised', layer: 'ag2' },
  taskcard: { sub: 'Subagent', label: m.ag2_item_taskcard, layer: 'app' },
  deliverable: { sub: 'Subagent', label: m.ag2_item_deliverable, layer: 'app' },
  a2ui: { sub: 'A2UI', label: m.ag2_item_a2ui, layer: 'app' },
  note: { sub: 'Stream', label: m.ag2_item_note, layer: 'app' },
}
export const itemAg2 = (kind: string): Ag2Tag | null => ITEM_AG2[kind] || null

// Curated architecture map for the "Powered by AG2" page. layer: 'ag2' (the
// framework gives you this) vs 'app' (built on top of AG2).
// One row of the "Powered by AG2" map; `sub` is absent for app-layer rows. `name` is a
// plain string where it is an AG2 symbol and a message function where it is prose;
// `what` is always prose. So: identifier → string, prose → message.
export type Primitive = { sub?: string; name: string | (() => string); what: () => string; layer: 'ag2' | 'app' }

export const PRIMITIVES: Primitive[] = [
  { sub: 'Model', name: 'ag2.Agent', what: m.ag2_what_agent, layer: 'ag2' },
  { sub: 'Stream', name: 'Stream + EventLogWriter', what: m.ag2_what_stream, layer: 'ag2' },
  { sub: 'Memory', name: 'KnowledgeConfig + WorkingMemoryAggregate', what: m.ag2_what_memory, layer: 'ag2' },
  { sub: 'Tool', name: m.ag2_name_native_tools, what: m.ag2_what_tools, layer: 'ag2' },
  { sub: 'Subagent', name: 'subagents.run_task', what: m.ag2_what_subagents, layer: 'ag2' },
  { sub: 'HITL', name: 'context.input / hitl_hook', what: m.ag2_what_hitl, layer: 'ag2' },
  { sub: 'Voice', name: 'LiveAgent', what: m.ag2_what_voice, layer: 'ag2' },
  { sub: 'A2UI', name: 'autogen.beta.a2ui', what: m.ag2_what_a2ui, layer: 'ag2' },
  { sub: 'Observer', name: 'LoopDetector / TokenMonitor / custom', what: m.ag2_what_observers, layer: 'ag2' },
  { sub: 'Tool', name: 'extensions.docker.DockerEnvironment', what: m.ag2_what_docker, layer: 'ag2' },
  { sub: 'Model', name: 'response_schema', what: m.ag2_what_schema, layer: 'ag2' },
  { name: m.ag2_name_task_store, what: m.ag2_what_task_store, layer: 'app' },
  { name: m.ag2_name_gateway, what: m.ag2_what_gateway, layer: 'app' },
]

// One row's heading — an AG2 symbol reads verbatim, an app-layer name reads localized.
export const primitiveName = (p: Primitive): string =>
  typeof p.name === 'string' ? p.name : p.name()

export const AG2_DOCS = DOCS
