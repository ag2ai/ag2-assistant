// Owns the active thread's stream connection: opens /api/stream for a session,
// folds events into items, runs turns, and (for tasks) polls the durable panel.

import { get, writable } from 'svelte/store'
import { thread, taskPanel, sessions, inspectorEvents } from './store.js'
import { StreamClient } from './transport/stream.js'
import { VoiceController } from './transport/voice.js'
import { api } from './transport/api.js'
import { foldEvent, isBusy } from './project.js'

let client = null
let panelTimer = null

// Replay de-dup: the server replays the FULL history on every (re)connect, then
// sends a `ready` marker. The socket auto-reconnects on any drop (restart, sleep,
// network blip), so without care a reconnect re-folds the whole history onto the
// existing items → a duplicated timeline. We fold each connect's replay into a
// throwaway buffer and atomically swap it into `items` on `ready`; live events
// (after `ready`) fold straight into `items`. On reconnect the visible items stay
// put until the rebuilt-identical buffer swaps in — no flash, no duplicates.
let _replaying = false
let _replayBuf = []

// ---- voice ----
export const voice = writable({ active: false, status: 'off' })
let voiceCtl = null
let _voiceActive = false        // a voice session is running (mic state)
let _suppressStream = false     // suppress stream folding while voice drives the thread,
                                // and through the stop→reload window (cleared only by openThread)
let _vitem = null, _vrole = null, _vseq = 0
const _vkey = () => 'v' + ++_vseq

// Buffer raw {type,data} events for the AG2 Inspector (bounded ring). The stream
// already delivers every AG2 event here — we just keep them so the inspector can
// reveal the live AG2 activity behind the UI (no extra server work).
const _INSPECT_CAP = 600
function _inspect(ev) {
  inspectorEvents.update((buf) => {
    const next = buf.length >= _INSPECT_CAP ? buf.slice(buf.length - _INSPECT_CAP + 1) : buf
    return [...next, { ...ev, _t: Date.now(), _id: (next.at(-1)?._id || 0) + 1 }]
  })
}

export function openThread(kind, id) {
  closeThread()
  _suppressStream = false       // a fresh thread always folds its stream
  _replaying = true; _replayBuf = []   // first connect's replay buffers until `ready`
  const session = kind === 'task' ? 'task:' + id : id
  thread.set({ id, kind, session, items: [], busy: false })
  inspectorEvents.set([])       // fresh inspector buffer per thread

  client = new StreamClient(session, {
    // Each (re)connect re-replays the full history: buffer it afresh so a reconnect
    // rebuilds rather than double-folds onto the live items.
    onOpen: () => { _replaying = true; _replayBuf = [] },
    onEvent: (ev) => {
      _inspect(ev)
      if (_suppressStream) return
      if (_replaying) { foldEvent(_replayBuf, ev); return }   // replay → buffer, don't render half-built
      thread.update((t) => { foldEvent(t.items, ev); return { ...t, items: t.items, busy: isBusy(t.items) } })
    },
    // Replay complete → atomically adopt the freshly-rebuilt buffer as the timeline.
    onReady: () => {
      if (!_replaying) return
      _replaying = false
      const items = _replayBuf; _replayBuf = []
      thread.update((t) => ({ ...t, items, busy: isBusy(items) }))
    },
    onTurnEnd: () => thread.update((t) => ({ ...t, busy: false })),
    onError: (m) => thread.update((t) => {
      t.items.push({ id: Date.now(), kind: 'note', icon: 'x', text: m.message || 'error', alert: true })
      return { ...t, busy: false }
    }),
  }).connect()

  if (kind === 'task') {
    _markedSeen = false          // arm the "mark seen once finished" latch for this task
    loadPanel(id)                // also marks it seen if it's already/becomes terminal
    panelTimer = setInterval(() => loadPanel(id), 3000)
  } else {
    taskPanel.set(null)
  }
}

export function send(text, attachments = []) {
  if (!client || (!text.trim() && !attachments.length)) return
  thread.update((t) => ({ ...t, busy: true }))
  client.send(text, attachments)
  // Surface a brand-new chat in the drawer immediately — the sessions list is
  // only persisted server-side once the first turn completes (which can take a
  // while). The drawer poll merges this until the server reports it for real.
  const t = get(thread)
  if (t.kind === 'chat' && text.trim()) {
    sessions.update((list) =>
      list.some((s) => s.session_id === t.session)
        ? list
        : [{ session_id: t.session, preview: text.trim().slice(0, 80), updated: new Date().toISOString(), turns: 0 }, ...list]
    )
  }
}

export function answer(inquiryId, text) {
  if (client) client.answer(inquiryId, text)
}

// Send 👍/👎 + a (mandatory) reason on a generated item. `target` carries the
// stable id/kind plus context for the learner: { targetKind, targetId, content, request }.
export function feedback({ targetKind, targetId, sentiment, reason, content = '', request = '' }) {
  if (client) client.feedback({ target_kind: targetKind, target_id: targetId, sentiment, reason, content, request })
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
  // capture at the active provider's native rate (Gemini 16 kHz / OpenAI 24 kHz)
  let inputRate = 16000
  try { inputRate = (await api.voices()).input_rate || 16000 } catch {}
  _voiceActive = true; _suppressStream = true; _vitem = null; _vrole = null
  voice.set({ active: true, status: 'connecting' })
  voiceCtl = new VoiceController(query, {
    onState: (s, text) => {
      if (s === 'off') _voiceEnded()          // user stop OR server close → reload to canonical
      else voice.set({ active: true, status: text || s })
    },
    onTranscript: _voiceTranscript,
    onTurnEnd: () => { _vitem = null; _vrole = null; _setBusy(false) },  // close the bubble; next reply is fresh
    // Structured events (tool chips/cards, task cards, deliverables) fold through
    // the SAME reducer as the text stream — one projection, voice just adds spoken
    // transcript on top. Also feed the AG2 Inspector, exactly like the text path.
    onEvent: (ev) => { _inspect(ev); thread.update((t) => { foldEvent(t.items, ev); return { ...t, items: t.items } }) },
    onAudio: () => _setBusy(false),
  }, inputRate)
  const ok = await voiceCtl.start()
  if (!ok) { _voiceActive = false; _suppressStream = false; voice.set({ active: false, status: 'off' }) }
}

// Voice ended (user mic-off or server close). Keep the live voice bubbles in
// place (no reload, no scroll jump) — just suppress the stream a moment longer so
// the server's disconnect-flush (the persisted copy of the turns we already
// showed) doesn't fold in as duplicates, then resume normal folding for any
// later typed messages. (A real page refresh still loads the canonical history.)
function _voiceEnded() {
  voiceCtl = null; _voiceActive = false; _vitem = null; _vrole = null
  voice.set({ active: false, status: 'off' })
  thread.update((t) => ({ ...t, busy: false }))   // no lingering "thinking…" after hang-up
  setTimeout(() => { _suppressStream = false }, 2000)
}

export function stopVoice() {
  if (voiceCtl) voiceCtl.stop()   // → onState('off') → _voiceEnded (teardown + reload)
}

const _TERMINAL_TASK = new Set(['completed', 'failed', 'cancelled'])
let _markedSeen = false   // per-viewed-task latch: mark seen once, only after it finishes

async function loadPanel(id) {
  let panel
  try { panel = await api.task(id) } catch { return /* keep last panel on error */ }
  taskPanel.set(panel)
  // Clear the unread indicator once the task is finished — whether it was already
  // done when opened or completed while the user watched. Peeking at a still-running
  // task deliberately does NOT mark it seen, so its dot still fires when it finishes
  // if the user has navigated away. Latched so we call markSeen at most once.
  if (!_markedSeen && panel && _TERMINAL_TASK.has(panel.status)) {
    _markedSeen = true
    api.markSeen(id).catch(() => {})
  }
}
