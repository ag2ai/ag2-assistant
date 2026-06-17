// Owns the active thread's stream connection: opens /api/stream for a session,
// folds events into items, runs turns, and (for tasks) polls the durable panel.

import { thread, taskPanel } from './store.js'
import { StreamClient } from './transport/stream.js'
import { api } from './transport/api.js'
import { foldEvent } from './project.js'

let client = null
let panelTimer = null

export function openThread(kind, id) {
  closeThread()
  const session = kind === 'task' ? 'task:' + id : id
  thread.set({ id, kind, session, items: [], busy: false })

  client = new StreamClient(session, {
    onEvent: (ev) => thread.update((t) => { foldEvent(t.items, ev); return { ...t, items: t.items } }),
    onTurnEnd: () => thread.update((t) => ({ ...t, busy: false })),
    onError: (m) => thread.update((t) => {
      t.items.push({ id: Date.now(), kind: 'note', text: '⚠ ' + (m.message || 'error') })
      return { ...t, busy: false }
    }),
  }).connect()

  if (kind === 'task') {
    loadPanel(id)
    panelTimer = setInterval(() => loadPanel(id), 3000)
  } else {
    taskPanel.set(null)
  }
}

export function send(text) {
  if (!client || !text.trim()) return
  thread.update((t) => ({ ...t, busy: true }))
  client.send(text)
}

export function answer(inquiryId, text) {
  if (client) client.answer(inquiryId, text)
}

export function closeThread() {
  if (client) { client.close(); client = null }
  if (panelTimer) { clearInterval(panelTimer); panelTimer = null }
}

async function loadPanel(id) {
  try { taskPanel.set(await api.task(id)) } catch { /* keep last */ }
}
