// Fold AG2 `{type, data}` events into renderable thread items. This is the whole
// projection: the GUI is a view of the event stream. History (replay) and live
// use the same reducer, so they produce identical items.

import { cardFor } from './lib/toolcards.js'
import { fmtDateTime } from './lib/time.js'
import { applyA2UIMessage } from './lib/a2ui.js'

const tail = (s) => (s || '').split('.').pop()
let _seq = 0
const nid = () => ++_seq

function joinText(parts) {
  return (parts || [])
    .filter((p) => tail(p.__event__ || '') === 'TextInput')
    .map((p) => p.content || '')
    .join('\n')
    .trim()
}

export function addTool(items, name, card) {
  if (!name) return
  const pretty = prettyToolName(name)
  let last = items[items.length - 1]
  if (!(last && last.kind === 'tools')) {
    last = { id: nid(), kind: 'tools', names: [], cards: [] }
    items.push(last)
  }
  const e = last.names[last.names.length - 1]
  if (e && e.name === pretty) e.n++
  else last.names.push({ name: pretty, n: 1 })
  // A tool can contribute a card (file written, search run, …) — a projection of
  // the call's structured args, rendered alongside the chips. See lib/toolcards.js.
  if (card) (last.cards ||= []).push({ id: nid(), ...card })
}

function prettyToolName(name) {
  if (name.startsWith('repo_files_')) {
    return 'repo-files · ' + name.slice('repo_files_'.length)
  }
  return name.replace(/_/g, ' ')
}

function clearA2UIActionStatus(items) {
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'note' && items[i].a2uiActionPending) items.splice(i, 1)
  }
}

// Whether a turn is still in progress, derived from the items rather than a
// transient flag — so it's correct after reopening a chat mid-turn (where the
// turn_end frame went to the old, closed socket). Busy if the most recent
// decisive item is a user message (awaiting a reply) and not yet a finalized
// agent response. A streaming agent bubble is skipped (the thinking dots are
// already hidden once text is streaming).
export function isBusy(items) {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.ends) return false          // a stopped turn ends here — never "thinking" on replay
    if (it.kind === 'agent' && !it.streaming) return false
    if (it.kind === 'user') return true
  }
  return false
}

// Root task lifecycle notes. `icon` is a Lucide name (see Icon.svelte) rendered
// by Note.svelte — replaces the old ▶/✓/⚠/⏹ emoji glyphs.
const NOTE = {
  TaskStarted: { icon: 'zap', text: 'Task started' },
  TaskCompleted: { icon: 'check', text: 'Task completed' },
  TaskFailed: { icon: 'x', text: 'Task failed' },
  TaskCancelled: { icon: 'x', text: 'Task cancelled' },
}

const ROOT_AGENT_NAMES = new Set(['ag2-assistant'])

function isRootTaskEvent(d) {
  return !d.agent_name || ROOT_AGENT_NAMES.has(d.agent_name)
}

function subagentStatus(type) {
  if (type === 'TaskStarted') return 'running'
  if (type === 'TaskCompleted') return 'completed'
  if (type === 'TaskCancelled') return 'cancelled'
  return 'failed'
}

function errorText(error) {
  if (!error) return ''
  if (typeof error === 'string') return error
  return error.message || error.content || String(error)
}

function resultPreview(result) {
  if (!result) return ''
  const text = typeof result === 'string' ? result : JSON.stringify(result)
  return text.replace(/\s+/g, ' ').trim().slice(0, 240)
}

// Find the subagent card for `taskId` (creating an empty one if its inner events
// arrive before its TaskStarted). `items` is the nested array holding the card.
function ensureSubagent(items, taskId, agent) {
  let item = items.find((i) => i.kind === 'subagent' && i.taskId === taskId)
  if (!item) {
    item = { id: nid(), kind: 'subagent', taskId, agent: agent || 'subagent', objective: '', status: 'running', items: [] }
    items.push(item)
  }
  if (!item.items) item.items = []
  return item
}

function upsertSubagent(items, type, d) {
  const taskId = d.task_id || `${d.agent_name}:${d.objective || ''}`
  const item = ensureSubagent(items, taskId, d.agent_name)
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
export function queueMessage(items, text) {
  if (text) items.push({ id: nid(), kind: 'user', text, queued: true })
  return items
}

// Mutates and returns `items`. `wire` is {type, data}.
export function foldEvent(items, wire) {
  const t = tail(wire.type)
  const d = wire.data || {}
  const before = items.length

  switch (t) {
    // DrainedModelRequest is what a message fed to a running turn comes back as (AG2
    // re-emits the inbox it drained). It IS a ModelRequest — same bubble, and it lands
    // where the agent actually picked it up, mid-turn.
    case 'ModelRequest':
    case 'DrainedModelRequest': {
      const text = joinText(d.parts)
      if (!text) break
      // Button fallbacks are private instructions passed to the agent. Their
      // corresponding A2UIActionSubmitted event is the user-facing status.
      if (text.startsWith('[[A2UI_ACTION]]')) break
      // If we were showing this as queued, the agent has now picked it up: resolve that
      // bubble in place rather than pushing a duplicate. (Replay never has queued items —
      // they're live-only, so history stays purely what the server recorded.)
      const queued = items.find((i) => i.queued && i.text === text)
      if (queued) queued.queued = false
      else items.push({ id: nid(), kind: 'user', text })
      break
    }
    case 'ModelMessageChunk': {
      let cur = items[items.length - 1]
      if (!cur || cur.kind !== 'agent' || !cur.streaming) {
        cur = { id: nid(), kind: 'agent', text: '', streaming: true }
        items.push(cur)
      }
      cur.text += d.content || ''
      break
    }
    case 'ModelResponse': {
      const msg = d.message && d.message.content
      const calls = (d.tool_calls && d.tool_calls.calls) || []
      // An A2UI button dispatches an ordinary agent turn. Its status note belongs
      // only to that turn, not to the durable conversation history.
      if (msg || !calls.length) clearA2UIActionStatus(items)
      const cur = items[items.length - 1]
      const streaming = [...items].reverse().find((it) => it.kind === 'agent' && it.streaming)
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
    case 'ToolCallsEvent':
      for (const c of d.calls || []) addTool(items, c.name, cardFor(c.name, c.arguments))
      break
    case 'TaskCreated':
      items.push({ id: nid(), kind: 'taskcard', taskId: d.task_id, title: d.title, scheduled: d.kind === 'scheduled' })
      break
    case 'TaskScheduled':
      items.push({ id: nid(), kind: 'note', icon: 'clock', text: `Scheduled for ${fmtDateTime(d.scheduled_for)}${d.recurrence ? ' · ' + (d.recurrence_desc || 'repeats ' + d.recurrence) : ''}` })
      break
    case 'TaskStarted':
    case 'TaskCompleted':
    case 'TaskFailed':
    case 'TaskCancelled':
      if (isRootTaskEvent(d)) items.push({ id: nid(), kind: 'note', icon: NOTE[t].icon, text: NOTE[t].text })
      else upsertSubagent(items, t, d)
      break
    case 'SubagentTrace': {
      // One inner event from a subagent — nest it under that subagent's card by
      // folding it (recursively, same reducer) into the card's own items array.
      // A nested SubagentTrace inside `inner` nests another level → recursion.
      const card = ensureSubagent(items, d.subagent_id, tail((d.subagent_id || '').split(':').pop()))
      if (d.inner && d.inner.type) foldEvent(card.items, d.inner)
      break
    }
    case 'ObserverAlert':
      // A behaviour observer flagged something (e.g. a stuck/flailing turn) — show
      // it as a subtle warning note in the thread. De-dupe identical consecutive
      // alerts (an observer fires once but replay + live can both deliver it).
      if (items[items.length - 1]?.text !== d.message)
        items.push({ id: nid(), kind: 'note', icon: 'zap', text: d.message, alert: true })
      break
    case 'DeliverableProduced':
      items.push({ id: nid(), kind: 'deliverable', taskId: d.task_id, deliverableId: d.deliverable_id, description: d.description, preview: d.preview, path: d.path })
      break
    case 'ImageGenerated':
      items.push({ id: nid(), kind: 'genimage', path: d.path, prompt: d.prompt })
      break
    case 'A2UISurface':
      {
        let item = items.find((i) => i.kind === 'a2ui' && i.surfaceId === d.surface_id)
        if (!item) {
          item = { id: nid(), kind: 'a2ui' }
          items.push(item)
        }
        Object.assign(item, {
          version: d.version,
          catalogId: d.catalog_id,
          surfaceId: d.surface_id,
          title: d.title,
          intent: d.intent,
          component: d.component || {},
          components: (d.component && d.component._components) || d.components || [],
          data: d.data || {},
        })
      }
      break
    case 'A2UISurfaceDataUpdated': {
      const item = items.find((i) => i.kind === 'a2ui' && i.surfaceId === d.surface_id)
      if (item) item.data = d.data || {}
      break
    }
    case 'A2UIActionSubmitted':
      // A surface can be clicked repeatedly, but only one action is in flight per
      // conversation. Replace the old indicator instead of accumulating spinners.
      clearA2UIActionStatus(items)
      items.push({ id: nid(), kind: 'note', icon: 'rotate-cw', pending: true, a2uiActionPending: true, surfaceId: d.surface_id, text: '' })
      break
    case 'A2UIMessageEvent': {
      applyA2UIMessage(items, d.message || d)
      break
    }
    case 'Attachment':
      items.push({ id: nid(), kind: 'attachment', path: d.path, name: d.name })
      break
    case 'InquiryRaised':
      items.push({ id: nid(), kind: 'inquiry', inquiryId: d.inquiry_id, question: d.question, detail: d.detail || '', options: d.options || [], qkind: d.kind, resolved: false })
      break
    case 'InquiryAnswered': {
      const it = items.find((i) => i.kind === 'inquiry' && i.inquiryId === d.inquiry_id)
      // `status` distinguishes a real answer from a timed-out (expired) or
      // task-cancelled prompt, so the card can retire its live buttons and say why.
      if (it) { it.resolved = true; it.answer = d.answer; it.resolution = d.status || 'answered' }
      break
    }
    case 'TurnCancelled': {
      // The user stopped the turn. Whatever it produced stays (tool chips, partial
      // text); finalize a mid-stream bubble so it reads as what was said before the
      // stop, and mark the turn ended (`ends` → isBusy stops here on replay too).
      const cur = items[items.length - 1]
      if (cur && cur.kind === 'agent' && cur.streaming) cur.streaming = false
      items.push({ id: nid(), kind: 'note', icon: 'x', text: d.reason || 'Stopped', ends: true })
      break
    }
    case 'TurnFailed': {
      // The turn errored (timeout / provider fault / connection drop). Same shape as
      // a stop — keep the work, finalize the bubble, end the turn — but flagged as an
      // alert so the thread says why it stopped instead of just ending mid-air.
      const cur = items[items.length - 1]
      if (cur && cur.kind === 'agent' && cur.streaming) cur.streaming = false
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
      // Project a 👍/👎 back onto the item it rated — match by the item's stable key
      // (message → created_at, image → path, deliverable → deliverable_id). Latest
      // event wins, so re-rating supersedes. Search newest-first.
      const k = d.target_kind, tid = d.target_id
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        const match =
          (k === 'message' && it.kind === 'agent' && String(it.at) === tid) ||
          (k === 'image' && it.kind === 'genimage' && it.path === tid) ||
          (k === 'deliverable' && it.kind === 'deliverable' && it.deliverableId === tid)
        if (match) { it.feedback = { sentiment: d.sentiment, reason: d.reason }; break }
      }
      break
    }
    case 'FeedbackCleared': {
      // Retraction: fold the thumb back to neutral on the item it rated. Same
      // match-by-stable-key as FeedbackGiven; folds in stream order so a clear after
      // a mark wins (and a later re-rating would win over this).
      const k = d.target_kind, tid = d.target_id
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        const match =
          (k === 'message' && it.kind === 'agent' && String(it.at) === tid) ||
          (k === 'image' && it.kind === 'genimage' && it.path === tid) ||
          (k === 'deliverable' && it.kind === 'deliverable' && it.deliverableId === tid)
        if (match) { it.feedback = null; break }
      }
      break
    }
    default:
      break // UsageEvent, ToolResult*, GeminiToolCallEvent, etc. — not rendered
  }
  // Stamp newly-appended items with the event's production time. Every AG2 event
  // carries `created_at` (Unix seconds) on the wire; it's persisted and survives
  // replay, so this is the true "when produced" — not a render-time clock. `??=`
  // leaves coalesced/streamed items (agent chunks, merged tool cards) on their
  // first event's time.
  for (let i = before; i < items.length; i++) items[i].at ??= d.created_at
  return items
}
