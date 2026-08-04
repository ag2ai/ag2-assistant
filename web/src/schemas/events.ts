// AG2 wire events. `type` arrives fully qualified (….ModelResponse); project.ts
// narrows on its tail. Each known event pins the fields the reducer reads; an
// unknown type falls through to the catch-all so a new backend event never
// breaks the UI.
import { z } from 'zod'
import type { KeyedToolCard as ToolCard } from '../lib/toolcards.ts'
import type { A2UIComponent } from '../lib/a2ui.ts'

const TextPart = z.object({ __event__: z.string().optional(), content: z.string().optional() })

// The `data` shape per event the reducer folds. project.ts parses with these
// AFTER narrowing on tail(type) — WireEvent itself never rejects a type.
export const EventData = {
  ModelRequest: z.object({ parts: z.array(TextPart).optional() }),
  ModelMessageChunk: z.object({ content: z.string().optional() }),
  ModelResponse: z.object({
    message: z.object({ content: z.string().optional() }).nullish(),
    tool_calls: z.object({ calls: z.array(z.unknown()).optional() }).nullish(),
  }),
  ToolCallsEvent: z.object({
    calls: z.array(z.object({ name: z.string(), arguments: z.unknown().optional() })).optional(),
  }),
  TaskCreated: z.object({
    task_id: z.string(),
    title: z.string().optional(),
    kind: z.string().optional(),
  }),
  TaskScheduled: z.object({
    scheduled_for: z.string().optional(),
    recurrence: z.string().optional(),
    recurrence_desc: z.string().optional(),
  }),
  // Shared by TaskStarted / TaskCompleted / TaskFailed / TaskCancelled. A root
  // task event has no agent_name; anything else folds into a subagent card, which
  // is why agent_name and objective matter here.
  TaskLifecycle: z.object({
    agent_name: z.string().optional(),
    task_id: z.string().optional(),
    objective: z.string().optional(),
    result: z.unknown().optional(),
    error: z.unknown().optional(),
    reason: z.string().optional(),
  }),
  SubagentTrace: z.object({
    subagent_id: z.string().optional(),
    inner: z.looseObject({ type: z.string() }).nullish(),
  }),
  ObserverAlert: z.object({ message: z.string() }),
  DeliverableProduced: z.object({
    task_id: z.string().optional(),
    deliverable_id: z.string(),
    description: z.string().optional(),
    preview: z.string().optional(),
    path: z.string().optional(),
  }),
  ImageGenerated: z.object({ path: z.string(), prompt: z.string().optional() }),
  A2UISurface: z.object({
    surface_id: z.string(),
    version: z.string().optional(),
    catalog_id: z.string().optional(),
    title: z.string().optional(),
    intent: z.string().optional(),
    component: z.record(z.string(), z.unknown()).optional(),
    components: z.array(z.unknown()).optional(),
    data: z.record(z.string(), z.unknown()).optional(),
  }),
  A2UISurfaceDataUpdated: z.object({
    surface_id: z.string(),
    data: z.record(z.string(), z.unknown()).optional(),
  }),
  A2UIActionSubmitted: z.object({ surface_id: z.string() }),
  // ag2 A2UIMessageEvent carries exactly one canonical A2UI message dict.
  A2UIMessageEvent: z.object({ message: z.record(z.string(), z.unknown()) }),
  Attachment: z.object({ path: z.string(), name: z.string().optional() }),
  InquiryRaised: z.object({
    inquiry_id: z.string(),
    question: z.string(),
    detail: z.string().optional(),
    options: z.array(z.string()).optional(),
    kind: z.string().optional(),
  }),
  InquiryAnswered: z.object({
    inquiry_id: z.string(),
    answer: z.string().optional(),
    status: z.string().optional(),
  }),
  TurnCancelled: z.object({ reason: z.string().optional() }),
  TurnFailed: z.object({ error: z.string().optional() }),
  // FeedbackGiven and FeedbackCleared share this payload; only the clear ignores
  // sentiment. target_kind picks which item key target_id matches on.
  FeedbackGiven: z.object({
    target_kind: z.enum(['message', 'image', 'deliverable']),
    target_id: z.string(),
    sentiment: z.string().optional(),
    reason: z.string().optional(),
  }),
} as const

// gateway/wire.py to_wire sends exactly {type, data} — nothing else.
export const WireEvent = z.object({
  type: z.string(),
  data: z.record(z.string(), z.unknown()).default({}),
})
export type WireEvent = z.infer<typeof WireEvent>

// Every event's data carries its production time (ag2 BaseEvent.created_at, Unix
// seconds); the reducer stamps the items an event produces with it.
export const EventMeta = z.object({ created_at: z.number().optional() })
export type EventMeta = z.infer<typeof EventMeta>

// The event names project.ts folds; anything else is ignored by design.
export const HANDLED_EVENTS = [
  'ModelRequest', 'DrainedModelRequest', 'ModelMessageChunk', 'ModelResponse',
  'ToolCallsEvent', 'TaskCreated', 'TaskScheduled', 'TaskStarted', 'TaskCompleted',
  'TaskFailed', 'TaskCancelled', 'SubagentTrace', 'ObserverAlert', 'DeliverableProduced',
  'ImageGenerated', 'A2UISurface', 'A2UISurfaceDataUpdated', 'A2UIActionSubmitted',
  'A2UIMessageEvent', 'Attachment', 'InquiryRaised', 'InquiryAnswered', 'TurnCancelled',
  'TurnFailed', 'FeedbackGiven', 'FeedbackCleared',
] as const
export type HandledEvent = (typeof HANDLED_EVENTS)[number]

// Frames the stream socket receives. stream_bridge.py stamps `chat` (the stream
// id) on ready/turn_end/error; the failure text rides in `message`, not `error`.
export const EventFrame = z.object({ event: WireEvent })
export type EventFrame = z.infer<typeof EventFrame>

export const ReadyFrame = z.object({ type: z.literal('ready'), chat: z.string().optional() })
export type ReadyFrame = z.infer<typeof ReadyFrame>

// `role` comes from the voice socket, which reuses this frame (app.py:3565).
export const TurnEndFrame = z.object({
  type: z.literal('turn_end'),
  chat: z.string().optional(),
  role: z.string().optional(),
})
export type TurnEndFrame = z.infer<typeof TurnEndFrame>

export const QueuedFrame = z.object({
  type: z.literal('queued'),
  text: z.string().optional(),
  chat: z.string().optional(),
})
export type QueuedFrame = z.infer<typeof QueuedFrame>

export const ErrorFrame = z.object({
  type: z.literal('error'),
  message: z.string().optional(),
  chat: z.string().optional(),
})
export type ErrorFrame = z.infer<typeof ErrorFrame>

export const ControlFrame = z.discriminatedUnion('type', [
  ReadyFrame,
  TurnEndFrame,
  QueuedFrame,
  ErrorFrame,
])
export type ControlFrame = z.infer<typeof ControlFrame>

export const ServerFrame = z.union([EventFrame, ControlFrame])
export type ServerFrame = z.infer<typeof ServerFrame>

// Frames the voice socket receives on top of the shared event frame: the same
// ready/turn_end/error plus its own live transcript (app.py:3565-3616).
export const TranscriptFrame = z.object({
  type: z.literal('transcript'),
  role: z.enum(['user', 'agent']),
  text: z.string(),
  final: z.boolean().optional(),
})
export type TranscriptFrame = z.infer<typeof TranscriptFrame>

export const VoiceFrame = z.union([
  EventFrame,
  z.discriminatedUnion('type', [ReadyFrame, TranscriptFrame, TurnEndFrame, ErrorFrame]),
])
export type VoiceFrame = z.infer<typeof VoiceFrame>

// One attachment a turn frame carries: the composer base64-encodes the file and the
// gateway decodes it into an AG2 input (app.py:3689 _decode_attachments).
export type AttachmentPayload = { name: string; mime: string; data: string }

// Frames the client sends. A turn is the bare {text, attachments} shape.
export type ClientFrame =
  // `model` rides only the message that CREATES a chat: a Text model chosen
  // before the chat existed, adopted as its override (ADR 0025).
  | { text: string; attachments?: AttachmentPayload[]; model?: string }
  | { type: 'cancel' }
  | { type: 'answer'; id: string; answer: string }
  | { type: 'feedback'; [k: string]: unknown }
  | { type: 'feedback_clear'; [k: string]: unknown }
  | { type: 'a2ui'; message: unknown }

// Thread items — the projection project.ts folds events into. These never cross
// the wire, so they are plain types rather than schemas.
export type Feedback = { sentiment?: string; reason?: string } | null

// `id` keys the rendered list; every producer takes it from lib/ids.ts so items
// from the reducer, the controller and a2ui.js never collide. `at` is the source
// event's created_at (Unix seconds).
type ItemBase = { id: number; at?: number }

// One card per tool call the thread renders alongside the chips — the union
// lib/toolcards.ts builds, keyed by `id` for the rendered list.
export type { ToolCard }

export type ThreadItem =
  // `voice` marks a bubble spoken in a live voice session — UserMessage/AgentMessage style it.
  | (ItemBase & { kind: 'user'; text: string; queued?: boolean; voice?: boolean })
  | (ItemBase & { kind: 'agent'; text: string; streaming?: boolean; empty?: boolean; voice?: boolean; feedback?: Feedback })
  | (ItemBase & { kind: 'tools'; names: { name: string; n: number }[]; cards: ToolCard[] })
  | (ItemBase & { kind: 'note'; icon: string; text: string; alert?: boolean; ends?: boolean; pending?: boolean; a2uiActionPending?: boolean; surfaceId?: string })
  | (ItemBase & { kind: 'taskcard'; taskId: string; title?: string; scheduled: boolean })
  | (ItemBase & { kind: 'subagent'; taskId: string; agent: string; objective: string; status?: string; result?: string; error?: string; items: ThreadItem[] })
  | (ItemBase & { kind: 'deliverable'; taskId?: string; deliverableId: string; description?: string; preview?: string; path?: string; feedback?: Feedback })
  | (ItemBase & { kind: 'genimage'; path: string; prompt?: string; feedback?: Feedback })
  | (ItemBase & { kind: 'attachment'; path: string; name?: string })
  | (ItemBase & { kind: 'inquiry'; inquiryId: string; question: string; detail: string; options: string[]; qkind?: string; resolved: boolean; answer?: string; resolution?: string })
  | (ItemBase & { kind: 'a2ui'; surfaceId: string; version?: string; catalogId?: string; title?: string; intent?: string; component: A2UIComponent; components?: A2UIComponent[]; data: Record<string, unknown>; messages?: unknown[] })
