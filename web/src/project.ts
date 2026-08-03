// Fold AG2 `{type, data}` events into renderable thread items. This is the whole
// projection: the GUI is a view of the event stream. History (replay) and live
// use the same reducer, so they produce identical items.

import { cardFor } from './lib/toolcards.ts'
import type { ToolCard } from './lib/toolcards.ts'
import { fmtDateTime } from './lib/time.ts'
import { applyA2UIMessage } from './lib/a2ui.ts'
import { nextItemId as nid } from './lib/ids.ts'
import { EventData, EventMeta, HANDLED_EVENTS, WireEvent } from './schemas/events.ts'
import type { HandledEvent, ThreadItem, WireEvent as Wire } from './schemas/events.ts'
import type { z } from 'zod'

type ItemOf<K extends ThreadItem['kind']> = Extract<ThreadItem, { kind: K }>
type Lifecycle = z.infer<typeof EventData.TaskLifecycle>
type TextParts = z.infer<typeof EventData.ModelRequest>['parts']

const tail = (s: string): string => s.split('.').pop() ?? s

function joinText(parts: TextParts): string {
  return (parts ?? [])
    .filter((p) => tail(p.__event__ ?? '') === 'TextInput')
    .map((p) => p.content ?? '')
    .join('\n')
    .trim()
}

export function addTool(items: ThreadItem[], name: string, card: ToolCard | null = null): void {
  if (!name) return
  const pretty = prettyToolName(name)
  const prev = items[items.length - 1]
  let last: ItemOf<'tools'>
  if (prev && prev.kind === 'tools') last = prev
  else {
    last = { id: nid(), kind: 'tools', names: [], cards: [] }
    items.push(last)
  }
  const e = last.names[last.names.length - 1]
  if (e && e.name === pretty) e.n++
  else last.names.push({ name: pretty, n: 1 })
  // A tool can contribute a card (file written, search run, …) — a projection of
  // the call's structured args, rendered alongside the chips. See lib/toolcards.js.
  if (card) last.cards.push({ ...card, id: nid() })
}

function prettyToolName(name: string): string {
  if (name.startsWith('repo_files_')) {
    return 'repo-files · ' + name.slice('repo_files_'.length)
  }
  return name.replace(/_/g, ' ')
}

function clearA2UIActionStatus(items: ThreadItem[]): void {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'note' && it.a2uiActionPending) items.splice(i, 1)
  }
}

// Whether a turn is still in progress, derived from the items rather than a
// transient flag — so it's correct after reopening a chat mid-turn (where the
// turn_end frame went to the old, closed socket). Busy if the most recent
// decisive item is a user message (awaiting a reply) and not yet a finalized
// agent response. A streaming agent bubble is skipped (the thinking dots are
// already hidden once text is streaming).
export function isBusy(items: ThreadItem[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'note' && it.ends) return false   // a stopped turn ends here — never "thinking" on replay
    if (it.kind === 'agent' && !it.streaming) return false
    if (it.kind === 'user') return true
  }
  return false
}

// Root task lifecycle notes. `icon` is a Lucide name (see Icon.svelte) rendered
// by Note.svelte — replaces the old ▶/✓/⚠/⏹ emoji glyphs.
type LifecycleEvent = 'TaskStarted' | 'TaskCompleted' | 'TaskFailed' | 'TaskCancelled'

const NOTE: Record<LifecycleEvent, { icon: string; text: string }> = {
  TaskStarted: { icon: 'zap', text: 'Task started' },
  TaskCompleted: { icon: 'check', text: 'Task completed' },
  TaskFailed: { icon: 'x', text: 'Task failed' },
  TaskCancelled: { icon: 'x', text: 'Task cancelled' },
}

const ROOT_AGENT_NAMES = new Set(['ag2-assistant'])

function isRootTaskEvent(d: Lifecycle): boolean {
  return !d.agent_name || ROOT_AGENT_NAMES.has(d.agent_name)
}

function subagentStatus(type: LifecycleEvent): string {
  if (type === 'TaskStarted') return 'running'
  if (type === 'TaskCompleted') return 'completed'
  if (type === 'TaskCancelled') return 'cancelled'
  return 'failed'
}

function errorText(error: unknown): string {
  if (!error) return ''
  if (typeof error === 'string') return error
  if (typeof error === 'object') {
    if ('message' in error && typeof error.message === 'string' && error.message) return error.message
    if ('content' in error && typeof error.content === 'string' && error.content) return error.content
  }
  return String(error)
}

function resultPreview(result: unknown): string {
  if (!result) return ''
  const text = typeof result === 'string' ? result : JSON.stringify(result)
  return text.replace(/\s+/g, ' ').trim().slice(0, 240)
}

// Find the subagent card for `taskId` (creating an empty one if its inner events
// arrive before its TaskStarted). `items` is the nested array holding the card.
function ensureSubagent(items: ThreadItem[], taskId: string, agent: string): ItemOf<'subagent'> {
  const found = items.find((i): i is ItemOf<'subagent'> => i.kind === 'subagent' && i.taskId === taskId)
  if (found) return found
  const item: ItemOf<'subagent'> = {
    id: nid(), kind: 'subagent', taskId, agent: agent || 'subagent', objective: '', status: 'running', items: [],
  }
  items.push(item)
  return item
}

function upsertSubagent(items: ThreadItem[], type: LifecycleEvent, d: Lifecycle): void {
  const taskId = d.task_id || `${d.agent_name}:${d.objective || ''}`
  const item = ensureSubagent(items, taskId, d.agent_name ?? '')
  item.status = subagentStatus(type)
  if (d.agent_name) item.agent = d.agent_name
  if (d.objective) item.objective = d.objective
  if (type === 'TaskCompleted') item.result = resultPreview(d.result)
  if (type === 'TaskFailed') item.error = errorText(d.error)
  if (type === 'TaskCancelled') item.error = d.reason || 'cancelled'
}

// A message the server fed to the running turn. AG2 won't echo it until the turn drains
// its inbox — which can be a whole tool round away — so show it now, marked queued, and
// let the drained event resolve it (see the ModelRequest case).
export function queueMessage(items: ThreadItem[], text: string): ThreadItem[] {
  if (text) items.push({ id: nid(), kind: 'user', text, queued: true })
  return items
}

const HANDLED: ReadonlySet<string> = new Set(HANDLED_EVENTS)

function isHandled(type: string): type is HandledEvent {
  return HANDLED.has(type)
}

// Reached only if a HANDLED_EVENTS name has no case below — a compile error there.
function assertNever(type: never): never {
  throw new Error(`unhandled event: ${String(type)}`)
}

// Mutates and returns `items`. `wire` is {type, data}.
export function foldEvent(items: ThreadItem[], wire: Wire): ThreadItem[] {
  const before = items.length
  try {
    const type = tail(wire.type)
    if (isHandled(type)) fold(items, type, wire.data)
  } catch (err) {
    // A single malformed event must not tear down the thread it renders into.
    console.warn(`[project] ${wire.type}`, err)
  }
  // Stamp newly-appended items with the event's production time. Every AG2 event
  // carries `created_at` (Unix seconds) in its data; it's persisted and survives
  // replay, so this is the true "when produced" — not a render-time clock. `??=`
  // leaves coalesced/streamed items (agent chunks, merged tool cards) on their
  // first event's time.
  const meta = EventMeta.safeParse(wire.data)
  const at = meta.success ? meta.data.created_at : undefined
  for (let i = before; i < items.length; i++) items[i].at ??= at
  return items
}

// The item kinds a 👍/👎 can land on (FeedbackGiven / FeedbackCleared).
type RatableItem = ItemOf<'agent' | 'genimage' | 'deliverable'>

// Match by the item's stable key: message → created_at, image → workspace path,
// deliverable → deliverable id.
function ratableMatch(item: ThreadItem, kind: string, targetId: string): item is RatableItem {
  if (kind === 'message') return item.kind === 'agent' && String(item.at) === targetId
  if (kind === 'image') return item.kind === 'genimage' && item.path === targetId
  if (kind === 'deliverable') return item.kind === 'deliverable' && item.deliverableId === targetId
  return false
}

// Each case parses its own payload schema: the event itself was already validated
// as {type, data} by the transport, but `data` arrives as an opaque record.
function fold(items: ThreadItem[], type: HandledEvent, data: Record<string, unknown>): void {
  switch (type) {
    // DrainedModelRequest is what a message fed to a running turn comes back as (AG2
    // re-emits the inbox it drained). It IS a ModelRequest — same bubble, and it lands
    // where the agent actually picked it up, mid-turn.
    case 'ModelRequest':
    case 'DrainedModelRequest': {
      const d = EventData.ModelRequest.parse(data)
      const text = joinText(d.parts)
      if (!text) break
      // Button fallbacks are private instructions passed to the agent. Their
      // corresponding A2UIActionSubmitted event is the user-facing status.
      if (text.startsWith('[[A2UI_ACTION]]')) break
      // If we were showing this as queued, the agent has now picked it up: resolve that
      // bubble in place rather than pushing a duplicate. (Replay never has queued items —
      // they're live-only, so history stays purely what the server recorded.)
      const queued = items.find((i): i is ItemOf<'user'> => i.kind === 'user' && !!i.queued && i.text === text)
      if (queued) queued.queued = false
      else items.push({ id: nid(), kind: 'user', text })
      break
    }
    case 'ModelMessageChunk': {
      const d = EventData.ModelMessageChunk.parse(data)
      const prev = items[items.length - 1]
      if (prev && prev.kind === 'agent' && prev.streaming) prev.text += d.content ?? ''
      else items.push({ id: nid(), kind: 'agent', text: d.content ?? '', streaming: true })
      break
    }
    case 'ModelResponse': {
      const d = EventData.ModelResponse.parse(data)
      const msg = d.message?.content
      const calls = d.tool_calls?.calls ?? []
      // An A2UI button dispatches an ordinary agent turn. Its status note belongs
      // only to that turn, not to the durable conversation history.
      if (msg || !calls.length) clearA2UIActionStatus(items)
      const streaming = [...items]
        .reverse()
        .find((it): it is ItemOf<'agent'> => it.kind === 'agent' && !!it.streaming)
      if (msg) {
        if (streaming) {
          streaming.text = msg
          streaming.streaming = false
        } else {
          items.push({ id: nid(), kind: 'agent', text: msg })
        }
      } else if (!calls.length) {
        // Final response with neither text nor tool calls → the turn ended without
        // a reply. Render a placeholder so the thread doesn't hang on "…" forever
        // (isBusy needs a finalized agent item to clear). Intermediate tool-calling
        // responses (calls.length > 0) are skipped — the turn isn't over.
        if (streaming) {
          streaming.text = '_(no reply)_'
          streaming.streaming = false
          streaming.empty = true
        } else {
          items.push({ id: nid(), kind: 'agent', text: '_(no reply)_', empty: true })
        }
      }
      break
    }
    // Only the batch event carries the calls; the per-provider ToolCallEvent
    // duplicates it (same ids), so we ignore the singular to avoid double chips.
    case 'ToolCallsEvent': {
      const d = EventData.ToolCallsEvent.parse(data)
      for (const c of d.calls ?? []) addTool(items, c.name, cardFor(c.name, c.arguments))
      break
    }
    case 'TaskCreated': {
      const d = EventData.TaskCreated.parse(data)
      items.push({ id: nid(), kind: 'taskcard', taskId: d.task_id, title: d.title, scheduled: d.kind === 'scheduled' })
      break
    }
    case 'TaskScheduled': {
      const d = EventData.TaskScheduled.parse(data)
      const repeats = d.recurrence ? ' · ' + (d.recurrence_desc || 'repeats ' + d.recurrence) : ''
      items.push({ id: nid(), kind: 'note', icon: 'clock', text: `Scheduled for ${fmtDateTime(d.scheduled_for)}${repeats}` })
      break
    }
    case 'TaskStarted':
    case 'TaskCompleted':
    case 'TaskFailed':
    case 'TaskCancelled': {
      const d = EventData.TaskLifecycle.parse(data)
      if (isRootTaskEvent(d)) items.push({ id: nid(), kind: 'note', icon: NOTE[type].icon, text: NOTE[type].text })
      else upsertSubagent(items, type, d)
      break
    }
    case 'SubagentTrace': {
      // One inner event from a subagent — nest it under that subagent's card by
      // folding it (recursively, same reducer) into the card's own items array.
      // A nested SubagentTrace inside `inner` nests another level → recursion.
      const d = EventData.SubagentTrace.parse(data)
      const sid = d.subagent_id ?? ''
      const card = ensureSubagent(items, sid, tail(sid.split(':').pop() ?? ''))
      if (d.inner) foldEvent(card.items, WireEvent.parse(d.inner))
      break
    }
    case 'ObserverAlert': {
      // A behaviour observer flagged something (e.g. a stuck/flailing turn) — show
      // it as a subtle warning note in the thread. De-dupe identical consecutive
      // alerts (an observer fires once but replay + live can both deliver it).
      const d = EventData.ObserverAlert.parse(data)
      const prev = items[items.length - 1]
      const prevText = prev && 'text' in prev ? prev.text : undefined
      if (prevText !== d.message) items.push({ id: nid(), kind: 'note', icon: 'zap', text: d.message, alert: true })
      break
    }
    case 'DeliverableProduced': {
      const d = EventData.DeliverableProduced.parse(data)
      items.push({
        id: nid(), kind: 'deliverable', taskId: d.task_id, deliverableId: d.deliverable_id,
        description: d.description, preview: d.preview, path: d.path,
      })
      break
    }
    case 'ImageGenerated': {
      const d = EventData.ImageGenerated.parse(data)
      items.push({ id: nid(), kind: 'genimage', path: d.path, prompt: d.prompt })
      break
    }
    case 'A2UISurface': {
      const d = EventData.A2UISurface.parse(data)
      let item = items.find((i): i is ItemOf<'a2ui'> => i.kind === 'a2ui' && i.surfaceId === d.surface_id)
      if (!item) {
        item = { id: nid(), kind: 'a2ui', surfaceId: d.surface_id, component: {}, data: {} }
        items.push(item)
      }
      // The tree can arrive nested under the root component or as its own list.
      const nested = d.component?._components
      item.version = d.version
      item.catalogId = d.catalog_id
      item.title = d.title
      item.intent = d.intent
      item.component = d.component ?? {}
      item.components = Array.isArray(nested) ? nested : d.components ?? []
      item.data = d.data ?? {}
      break
    }
    case 'A2UISurfaceDataUpdated': {
      const d = EventData.A2UISurfaceDataUpdated.parse(data)
      const item = items.find((i): i is ItemOf<'a2ui'> => i.kind === 'a2ui' && i.surfaceId === d.surface_id)
      if (item) item.data = d.data ?? {}
      break
    }
    case 'A2UIActionSubmitted': {
      // A surface can be clicked repeatedly, but only one action is in flight per
      // conversation. Replace the old indicator instead of accumulating spinners.
      const d = EventData.A2UIActionSubmitted.parse(data)
      clearA2UIActionStatus(items)
      items.push({
        id: nid(), kind: 'note', icon: 'rotate-cw', pending: true,
        a2uiActionPending: true, surfaceId: d.surface_id, text: '',
      })
      break
    }
    case 'A2UIMessageEvent': {
      const d = EventData.A2UIMessageEvent.parse(data)
      applyA2UIMessage(items, d.message)
      break
    }
    case 'Attachment': {
      const d = EventData.Attachment.parse(data)
      items.push({ id: nid(), kind: 'attachment', path: d.path, name: d.name })
      break
    }
    case 'InquiryRaised': {
      const d = EventData.InquiryRaised.parse(data)
      items.push({
        id: nid(), kind: 'inquiry', inquiryId: d.inquiry_id, question: d.question,
        detail: d.detail ?? '', options: d.options ?? [], qkind: d.kind, resolved: false,
      })
      break
    }
    case 'InquiryAnswered': {
      const d = EventData.InquiryAnswered.parse(data)
      const it = items.find((i): i is ItemOf<'inquiry'> => i.kind === 'inquiry' && i.inquiryId === d.inquiry_id)
      // `status` distinguishes a real answer from a timed-out (expired) or
      // task-cancelled prompt, so the card can retire its live buttons and say why.
      if (it) { it.resolved = true; it.answer = d.answer; it.resolution = d.status || 'answered' }
      break
    }
    case 'TurnCancelled': {
      // The user stopped the turn. Whatever it produced stays (tool chips, partial
      // text); finalize a mid-stream bubble so it reads as what was said before the
      // stop, and mark the turn ended (`ends` → isBusy stops here on replay too).
      const d = EventData.TurnCancelled.parse(data)
      const prev = items[items.length - 1]
      if (prev && prev.kind === 'agent' && prev.streaming) prev.streaming = false
      items.push({ id: nid(), kind: 'note', icon: 'x', text: d.reason || 'Stopped', ends: true })
      break
    }
    case 'TurnFailed': {
      // The turn errored (timeout / provider fault / connection drop). Same shape as
      // a stop — keep the work, finalize the bubble, end the turn — but flagged as an
      // alert so the thread says why it stopped instead of just ending mid-air.
      const d = EventData.TurnFailed.parse(data)
      const prev = items[items.length - 1]
      if (prev && prev.kind === 'agent' && prev.streaming) prev.streaming = false
      items.push({
        id: nid(),
        kind: 'note',
        icon: 'x',
        text: d.error || 'The turn failed unexpectedly.',
        alert: true,
        ends: true,
      })
      break
    }
    case 'FeedbackGiven': {
      // Project a 👍/👎 back onto the item it rated. Latest event wins, so re-rating
      // supersedes. Search newest-first.
      const d = EventData.FeedbackGiven.parse(data)
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (ratableMatch(it, d.target_kind, d.target_id)) {
          it.feedback = { sentiment: d.sentiment, reason: d.reason }
          break
        }
      }
      break
    }
    case 'FeedbackCleared': {
      // Retraction: fold the thumb back to neutral on the item it rated. Same
      // match-by-stable-key as FeedbackGiven; folds in stream order so a clear after
      // a mark wins (and a later re-rating would win over this).
      const d = EventData.FeedbackGiven.parse(data)
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (ratableMatch(it, d.target_kind, d.target_id)) {
          it.feedback = null
          break
        }
      }
      break
    }
    default:
      assertNever(type)
  }
}
