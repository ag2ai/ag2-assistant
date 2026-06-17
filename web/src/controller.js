// Owns the active thread's stream connection: opens /api/stream for a session,
// folds events into items, runs turns, and (for tasks) polls the durable panel.

import { get, writable } from 'svelte/store'
import { thread, taskPanel } from './store.js'
import { StreamClient } from './transport/stream.js'
import { VoiceController } from './transport/voice.js'
import { api } from './transport/api.js'
import { addTool, foldEvent } from './project.js'

let client = null
let panelTimer = null

// ---- voice ----
export const voice = writable({ active: false, status: 'off' })
let voiceCtl = null
let _voiceActive = false        // a voice session is running (mic state)
let _suppressStream = false     // suppress stream folding while voice drives the thread,
                                // and through the stop→reload window (cleared only by openThread)
let _vitem = null, _vrole = null, _vseq = 0
const _vkey = () => 'v' + ++_vseq

export function openThread(kind, id) {
  closeThread()
  _suppressStream = false       // a fresh thread always folds its stream
  const session = kind === 'task' ? 'task:' + id : id
  thread.set({ id, kind, session, items: [], busy: false })

  client = new StreamClient(session, {
    onEvent: (ev) => { if (_suppressStream) return; thread.update((t) => { foldEvent(t.items, ev); return { ...t, items: t.items } }) },
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

export function send(text, attachments = []) {
  if (!client || (!text.trim() && !attachments.length)) return
  thread.update((t) => ({ ...t, busy: true }))
  client.send(text, attachments)
}

export function answer(inquiryId, text) {
  if (client) client.answer(inquiryId, text)
}

export function closeThread() {
  if (voiceCtl) { voiceCtl.stop(); voiceCtl = null }  // _voiceEnded's reload is guarded out on nav
  _voiceActive = false; _vitem = null; _vrole = null
  if (client) { client.close(); client = null }
  if (panelTimer) { clearInterval(panelTimer); panelTimer = null }
}

// ---- voice: frames render into the SAME thread (transcripts as bubbles, tool
// chips, task cards). While active we suppress stream folding so the agent's
// delegated work (which also lands on this session's stream) isn't double-shown. ----
function _setBusy(b) { thread.update((t) => ({ ...t, busy: b })) }

function _voiceTranscript(role, text, final) {
  thread.update((t) => {
    if (final && role === 'user') {
      if (_vitem && _vrole === 'user') _vitem.text = text
      else { _vitem = { id: _vkey(), kind: 'user', text, voice: true }; t.items.push(_vitem) }
      _vitem = null; _vrole = null
      return { ...t, items: t.items, busy: true }   // thinking until the agent replies
    }
    if (_vitem && _vrole === role) { _vitem.text += text }
    else {
      _vitem = { id: _vkey(), kind: role === 'user' ? 'user' : 'agent', text, voice: true }
      t.items.push(_vitem); _vrole = role
    }
    return { ...t, items: t.items }
  })
}

export async function startVoice() {
  const t = get(thread)
  if (!t.id || _voiceActive) return
  const query = t.kind === 'task' ? '?task=' + encodeURIComponent(t.id) : '?session=' + encodeURIComponent(t.session)
  _voiceActive = true; _suppressStream = true; _vitem = null; _vrole = null
  voice.set({ active: true, status: 'connecting' })
  voiceCtl = new VoiceController(query, {
    onState: (s, text) => {
      if (s === 'off') _voiceEnded()          // user stop OR server close → reload to canonical
      else voice.set({ active: true, status: text || s })
    },
    onTranscript: _voiceTranscript,
    onTool: (name) => { _setBusy(true); thread.update((t) => { addTool(t.items, name); return { ...t, items: t.items } }) },
    onTaskCard: (m) => { _setBusy(false); thread.update((t) => { t.items.push({ id: _vkey(), kind: 'taskcard', taskId: m.id, title: m.title }); return { ...t, items: t.items } }) },
    onAudio: () => _setBusy(false),
  })
  const ok = await voiceCtl.start()
  if (!ok) { _voiceActive = false; _suppressStream = false; voice.set({ active: false, status: 'off' }) }
}

// Voice ended (user mic-off or server close). Reset mic state and reload the
// thread so the transient live bubbles are replaced by the canonical persisted
// conversation. _suppressStream stays TRUE until that reload (openThread clears
// it), so the server's disconnect-flush never flashes a duplicate in between.
function _voiceEnded() {
  voiceCtl = null; _voiceActive = false; _vitem = null; _vrole = null
  voice.set({ active: false, status: 'off' })
  const t = get(thread)
  if (t.id) setTimeout(() => { if (get(thread).id === t.id) openThread(t.kind, t.id) }, 900)
}

export function stopVoice() {
  if (voiceCtl) voiceCtl.stop()   // → onState('off') → _voiceEnded (teardown + reload)
}

async function loadPanel(id) {
  try { taskPanel.set(await api.task(id)) } catch { /* keep last */ }
}
