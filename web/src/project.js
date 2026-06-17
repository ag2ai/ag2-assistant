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

function addTool(items, name) {
  if (!name) return
  const pretty = name.replace(/_/g, ' ')
  const last = items[items.length - 1]
  if (last && last.kind === 'tools') {
    const e = last.names[last.names.length - 1]
    if (e && e.name === pretty) e.n++
    else last.names.push({ name: pretty, n: 1 })
  } else {
    items.push({ id: nid(), kind: 'tools', names: [{ name: pretty, n: 1 }] })
  }
}

const NOTE = {
  TaskStarted: '▶ Task started',
  TaskCompleted: '✓ Task completed',
  TaskFailed: '⚠ Task failed',
  TaskCancelled: '⏹ Task cancelled',
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
      if (msg) {
        const cur = items[items.length - 1]
        if (cur && cur.kind === 'agent' && cur.streaming) {
          cur.text = msg
          cur.streaming = false
        } else {
          items.push({ id: nid(), kind: 'agent', text: msg })
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
      items.push({ id: nid(), kind: 'note', text: NOTE[t] })
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
