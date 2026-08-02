// Owns the active thread's stream connection: opens /api/stream for a chat,
// folds events into items, runs turns, and (for tasks) polls the durable panel.

import { get, writable } from 'svelte/store'
import { thread, runInfo, chats, tasks, inquiries, inspectorEvents, viewer, profiles, profileEpoch } from './store.js'
import { StreamClient } from './transport/stream.ts'
import { VoiceController } from './transport/voice.ts'
import { api } from './transport/api/index.ts'
import { foldEvent, isBusy, queueMessage } from './project.js'
import { getActiveProfileId, setActiveProfileId } from './lib/profile.js'
import { setAccent } from './design/palette.js'
import { go, closeAside, route } from './router.js'

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
  const chat = kind === 'run' ? 'task-run:' + id : id
  thread.set({ id, kind, chat, items: [], busy: false })
  inspectorEvents.set([])       // fresh inspector buffer per thread

  client = new StreamClient(chat, {
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
    // Fed into the turn already running: show it straight away (marked queued) so the
    // wait for the agent to reach its next step isn't silent.
    onQueued: (m) => thread.update((t) => {
      queueMessage(t.items, m.text)
      return { ...t, items: t.items, busy: true }
    }),
    onError: (m) => thread.update((t) => {
      t.items.push({ id: Date.now(), kind: 'note', icon: 'x', text: m.message || 'error', alert: true })
      return { ...t, busy: false }
    }),
  }).connect()

  if (kind === 'run') {
    _markedSeen = false
    // Clear the previous run's data before the fetch lands — loadRun keeps the
    // last value on error, so a stale runInfo could otherwise leak into this
    // thread's folder panel and point "Move to task" at the wrong task.
    runInfo.set(null)
    loadRun(id)
    panelTimer = setInterval(() => loadRun(id), 3000)
  } else {
    runInfo.set(null)
  }
}

export function send(text, attachments = []) {
  if (!client || (!text.trim() && !attachments.length)) return
  thread.update((t) => ({ ...t, busy: true }))
  client.send(text, attachments)
  // Surface a brand-new chat in the drawer immediately — the chats list is
  // only persisted server-side once the first turn completes (which can take a
  // while). The drawer poll merges this until the server reports it for real.
  const t = get(thread)
  if (t.kind === 'chat' && text.trim()) {
    chats.update((list) =>
      list.some((s) => s.chat_id === t.chat)
        ? list
        : [{ chat_id: t.chat, preview: text.trim().slice(0, 80), updated: new Date().toISOString(), turns: 0 }, ...list]
    )
  }
}

// Stop the running turn. Stays busy until the server confirms — the turn ends when
// the TurnCancelled event lands (or turn_end arrives), not when we ask.
export function stop() {
  if (client) client.cancel()
}

export function answer(inquiryId, text) {
  if (client) client.answer(inquiryId, text)
}

export function a2uiAction(message) {
  if (client) client.a2ui(message)
}

// Send 👍/👎 + a (mandatory) reason on a generated item. `target` carries the
// stable id/kind plus context for the learner: { targetKind, targetId, content, request }.
export function feedback({ targetKind, targetId, sentiment, reason, content = '', request = '' }) {
  if (client) client.feedback({ target_kind: targetKind, target_id: targetId, sentiment, reason, content, request })
}

// Retract a rating (toggle the 👍/👎 off). Clears only the visible thumb — the server
// emits FeedbackCleared and runs no learner, so learned memory is left untouched.
export function clearFeedback({ targetKind, targetId }) {
  if (client) client.clearFeedback({ target_kind: targetKind, target_id: targetId })
}

export function closeThread() {
  if (voiceCtl) { voiceCtl.stop(); voiceCtl = null }  // _voiceEnded's reload is guarded out on nav
  _voiceActive = false; _vitem = null; _vrole = null
  if (client) { client.close(); client = null }
  if (panelTimer) { clearInterval(panelTimer); panelTimer = null }
}

// Empty every profile-scoped store and bump profileEpoch, so a new profile starts
// clean without a page reload. Any store keyed to the active profile MUST be reset
// here; install-wide stores (llmConfigs, foldersStore, permissions) are left alone.
function resetProfileState() {
  closeThread()                                        // WS + panel timer + voice
  thread.set({ id: null, kind: 'chat', items: [], busy: false })
  chats.set([])
  tasks.set([])
  runInfo.set(null)
  inquiries.set([])
  inspectorEvents.set([])
  viewer.set(null)                                     // drop the old profile's transient preview body
  profileEpoch.update((n) => n + 1)
}

// Switch the active profile in place (no reload → the Settings modal doesn't blink).
// Adopt the pid + accent before the route change so the new chat's WebSocket scopes
// to it; go('/') lands on the profile's home and preserves the hash. On any failure
// fall back to a full-page nav.
export function switchProfile(pid) {
  if (!pid || pid === getActiveProfileId()) return
  try {
    resetProfileState()
    // The file preview rail addresses a file in the OLD profile's workspace and lives
    // in the URL's aside slot, which go() preserves — strip it so the switch closes it.
    if (get(route).aside?.kind === 'file') closeAside()
    setActiveProfileId(pid)
    const p = (get(profiles).list || []).find((x) => x.id === pid)
    if (p?.accent) setAccent(p.accent)
    profiles.update((r) => ({ ...r, activeId: pid }))
    go('/', pid)
  } catch (e) {
    console.warn('[profile] in-place switch failed; falling back to reload', e)
    location.assign('/app/' + pid + '/' + location.hash)
  }
}

// ---- voice: frames render into the SAME thread (transcripts as bubbles, tool
// chips, task cards). While active we suppress stream folding so the agent's
// delegated work (which also lands on this chat's stream) isn't double-shown. ----
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
  const query = '?chat=' + encodeURIComponent(t.chat)
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

async function loadRun(id) {
  const epoch = get(profileEpoch)   // drop a poll that resolves after a profile switch
  let run
  try { run = await api.run(id) } catch { return /* keep last on error */ }
  if (get(profileEpoch) !== epoch) return
  // Thread-correlation guard: a fast run-A → run-B navigation can leave this
  // call's fetch resolving after the thread has already moved on. Without this,
  // A's late response would overwrite B's runInfo and could fire api.runSeen(A)
  // through the reset `_markedSeen` latch. Bail unless the thread is still `id`.
  const t = get(thread)
  if (!(t.kind === 'run' && t.id === id)) return
  runInfo.set(run)
  // Clear the unread indicator once the run is finished — whether it was already
  // done when opened or completed while the user watched. Peeking at a still-running
  // run deliberately does NOT mark it seen, so its dot still fires when it finishes
  // if the user has navigated away. Latched so we call runSeen at most once.
  if (!_markedSeen && run && _TERMINAL_TASK.has(run.status)) {
    _markedSeen = true
    api.runSeen(id).catch(() => {})
  }
}
