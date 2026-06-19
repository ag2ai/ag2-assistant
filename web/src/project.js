// Fold AG2 `{type, data}` events into renderable thread items. This is the whole
// projection: the GUI is a view of the event stream. History (replay) and live
// use the same reducer, so they produce identical items.

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

export function addTool(items, name) {
  if (!name) return
  const pretty = prettyToolName(name)
  const last = items[items.length - 1]
  if (last && last.kind === 'tools') {
    const e = last.names[last.names.length - 1]
    if (e && e.name === pretty) e.n++
    else last.names.push({ name: pretty, n: 1 })
  } else {
    items.push({ id: nid(), kind: 'tools', names: [{ name: pretty, n: 1 }] })
  }
}

function prettyToolName(name) {
  if (name.startsWith('repo_files_')) {
    return 'repo-files · ' + name.slice('repo_files_'.length)
  }
  return name.replace(/_/g, ' ')
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
    if (it.kind === 'agent' && !it.streaming) return false
    if (it.kind === 'user') return true
  }
  return false
}

const NOTE = {
  TaskStarted: '▶ Task started',
  TaskCompleted: '✓ Task completed',
  TaskFailed: '⚠ Task failed',
  TaskCancelled: '⏹ Task cancelled',
}

const ROOT_AGENT_NAMES = new Set(['ag2assistant', 'agclaw'])

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

// Mutates and returns `items`. `wire` is {type, data}.
export function foldEvent(items, wire) {
  const t = tail(wire.type)
  const d = wire.data || {}

  switch (t) {
    case 'ModelRequest': {
      const text = joinText(d.parts)
      if (text) items.push({ id: nid(), kind: 'user', text })
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
      const cur = items[items.length - 1]
      if (msg) {
        if (cur && cur.kind === 'agent' && cur.streaming) {
          cur.text = msg
          cur.streaming = false
        } else {
          items.push({ id: nid(), kind: 'agent', text: msg })
        }
      } else if (!calls.length) {
        // Final response with neither text nor tool calls → the turn ended without
        // a reply. Render a placeholder so the thread doesn't hang on "…" forever
        // (isBusy needs a finalized agent item to clear). Intermediate tool-calling
        // responses (calls.length > 0) are skipped — the turn isn't over.
        if (cur && cur.kind === 'agent' && cur.streaming) {
          cur.text = '_(no reply)_'
          cur.streaming = false
          cur.empty = true
        } else {
          items.push({ id: nid(), kind: 'agent', text: '_(no reply)_', empty: true })
        }
      }
      break
    }
    // Only the batch event carries the calls; the per-provider ToolCallEvent
    // duplicates it (same ids), so we ignore the singular to avoid double chips.
    case 'ToolCallsEvent':
      for (const c of d.calls || []) addTool(items, c.name)
      break
    case 'TaskCreated':
      items.push({ id: nid(), kind: 'taskcard', taskId: d.task_id, title: d.title, scheduled: d.kind === 'scheduled' })
      break
    case 'TaskScheduled':
      items.push({ id: nid(), kind: 'note', text: `⏰ Scheduled for ${d.scheduled_for}${d.recurrence ? ' · repeats ' + d.recurrence : ''}` })
      break
    case 'TaskStarted':
    case 'TaskCompleted':
    case 'TaskFailed':
    case 'TaskCancelled':
      if (isRootTaskEvent(d)) items.push({ id: nid(), kind: 'note', text: NOTE[t] })
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
      if (items[items.length - 1]?.text !== '⚠ ' + d.message)
        items.push({ id: nid(), kind: 'note', text: '⚠ ' + d.message, alert: true })
      break
    case 'DeliverableProduced':
      items.push({ id: nid(), kind: 'deliverable', taskId: d.task_id, deliverableId: d.deliverable_id, description: d.description, preview: d.preview })
      break
    case 'InquiryRaised':
      items.push({ id: nid(), kind: 'inquiry', inquiryId: d.inquiry_id, question: d.question, options: d.options || [], qkind: d.kind, resolved: false })
      break
    case 'InquiryAnswered': {
      const it = items.find((i) => i.kind === 'inquiry' && i.inquiryId === d.inquiry_id)
      if (it) { it.resolved = true; it.answer = d.answer }
      break
    }
    default:
      break // UsageEvent, ToolResult*, GeminiToolCallEvent, etc. — not rendered
  }
  return items
}
