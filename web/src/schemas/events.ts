// AG2 wire events. `type` arrives fully qualified (….ModelResponse); project.ts
// narrows on its tail. Each known event pins the fields the reducer reads; an
// unknown type falls through to the catch-all so a new backend event never
// breaks the UI.
import { z } from 'zod'

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
  A2UIMessageEvent: z.object({ message: z.unknown().optional() }),
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

// Every event also carries the production time the reducer stamps items with.
export const WireEvent = z.object({
  type: z.string(),
  data: z.record(z.string(), z.unknown()).default({}),
  created_at: z.number().optional(),
})
export type WireEvent = z.infer<typeof WireEvent>

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

// Frames the stream socket receives.
export const ServerFrame = z.union([
  z.object({ event: WireEvent }),
  z.object({ type: z.literal('ready'), chat: z.string().optional() }),
  z.object({ type: z.literal('turn_end') }),
  z.object({ type: z.literal('queued'), text: z.string().optional() }),
  z.object({ type: z.literal('error'), error: z.string().optional() }),
])
export type ServerFrame = z.infer<typeof ServerFrame>

// Frames the client sends. A turn is the bare {text, attachments} shape.
export type ClientFrame =
  | { text: string; attachments?: string[] }
  | { type: 'cancel' }
  | { type: 'answer'; id: string; answer: string }
  | { type: 'feedback'; [k: string]: unknown }
  | { type: 'feedback_clear'; [k: string]: unknown }
  | { type: 'a2ui'; message: unknown }

// Thread items — the projection project.ts folds events into. These never cross
// the wire, so they are plain types rather than schemas.
export type Feedback = { sentiment?: string; reason?: string } | null

type ItemBase = { id: number; at?: number }

// One card per tool call the thread renders alongside the chips. lib/toolcards.js
// builds them; task 18 replaces this with that module's own union.
export type ToolCard = { id: number } & Record<string, unknown>

export type ThreadItem =
  | (ItemBase & { kind: 'user'; text: string; queued?: boolean })
  | (ItemBase & { kind: 'agent'; text: string; streaming?: boolean; empty?: boolean; feedback?: Feedback })
  | (ItemBase & { kind: 'tools'; names: { name: string; n: number }[]; cards: ToolCard[] })
  | (ItemBase & { kind: 'note'; icon: string; text: string; alert?: boolean; ends?: boolean; pending?: boolean; a2uiActionPending?: boolean; surfaceId?: string })
  | (ItemBase & { kind: 'taskcard'; taskId: string; title?: string; scheduled: boolean })
  | (ItemBase & { kind: 'subagent'; taskId: string; agent: string; objective: string; status?: string; result?: string; error?: string; items: ThreadItem[] })
  | (ItemBase & { kind: 'deliverable'; taskId?: string; deliverableId: string; description?: string; preview?: string; path?: string; feedback?: Feedback })
  | (ItemBase & { kind: 'genimage'; path: string; prompt?: string; feedback?: Feedback })
  | (ItemBase & { kind: 'attachment'; path: string; name?: string })
  | (ItemBase & { kind: 'inquiry'; inquiryId: string; question: string; detail: string; options: string[]; qkind?: string; resolved: boolean; answer?: string; resolution?: string })
  | (ItemBase & { kind: 'a2ui'; surfaceId: string; version?: string; catalogId?: string; title?: string; intent?: string; component: Record<string, unknown>; components?: unknown[]; data: Record<string, unknown>; messages?: unknown[] })
