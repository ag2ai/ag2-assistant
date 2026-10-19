// Owns the active thread's connection: loads a chat's history, subscribes to its AG-UI
// events, runs turns, and (for tasks) polls the durable panel.

import { get, writable, type Writable } from 'svelte/store'
import type { BaseEvent } from '@ag-ui/client'
import { thread, runInfo, chats, screenRows, tasks, inquiries, inspectorEvents, viewer, profiles, profileEpoch, chatModel, refreshFiles } from './store.ts'
import type { ThreadKind } from './store.ts'
import { NO_CHAT_MODEL, openedChat, sentFirstMessage } from './lib/chatModel.ts'
import { ChatWatch, runChat } from './transport/agui.ts'
import type { RunInput } from './transport/agui.ts'
import { VoiceController } from './transport/voice.ts'
import { api } from './transport/api/index.ts'
import { foldEvent, queueMessage } from './project.ts'
import { AguiState, foldAgui, runningAfter } from './projectAgui.ts'
import { nextItemId } from './lib/ids.ts'
import { projectHistory } from './lib/chatHistory.ts'
import { errText } from './lib/errors.ts'
import type { A2UISurfaceState, AttachmentPayload, ThreadItem, WireEvent } from './schemas/events.ts'
import { getActiveProfileId, setActiveProfileId } from './lib/profile.ts'
import { setAccent } from './design/palette.ts'
import { go, closeAside, route } from './router.ts'
import { chatEventStatus, mergeChatStatus, pendingChatDeadlines } from './lib/chatActivity.ts'

// The two item kinds a voice session streams into (see _voiceTranscript).
type VoiceRole = 'user' | 'agent'
type VoiceBubble = Extract<ThreadItem, { kind: VoiceRole }>

let watch: ChatWatch | null = null
let stopRun: (() => void) | null = null
let panelTimer: ReturnType<typeof setInterval> | null = null

// The loaded range of the chat as AG-UI events; older pages prepend and the fold reruns.
let _historyEvents: BaseEvent[] = []
let _fold = new AguiState()
let _ready = false                 // the latest page is folded; live events fold straight in
let _buffer: BaseEvent[] = []      // live events that arrived while it was loading
let _session = 0
let _connection = 0
let _interrupts = new Set<string>()   // Inquiries that ended a run: answered by resuming it
export const chatHistory = writable<{
  chat: string | null; loading: boolean; loadingOlder: boolean; error: string;
  before: string | null; start: string | null
}>({ chat: null, loading: false, loadingOlder: false, error: '', before: null, start: null })

export async function loadOlderMessages(): Promise<boolean> {
  const state = get(chatHistory)
  if (!state.chat || !state.before || state.loading || state.loadingOlder || _voiceActive) return false
  const session = _session, connection = _connection
  chatHistory.update(s => ({ ...s, loadingOlder: true, error: '' }))
  try {
    const page = await api.chatEvents(state.chat, state.before)
    if (session !== _session || connection !== _connection) return false
    _historyEvents = [...(page.events as BaseEvent[]), ..._historyEvents]
    _fold = new AguiState()
    thread.update(t => ({ ...t, items: projectHistory(_historyEvents, t.items, _fold) }))
    chatHistory.update(s => ({ ...s, before: page.before, start: page.start ?? s.start, loadingOlder: false }))
    return true
  } catch (error) {
    if (session === _session && connection === _connection) {
      chatHistory.update(s => ({ ...s, loadingOlder: false, error: errText(error) }))
    }
    return false
  }
}

// ---- voice ----
export type VoiceStatus = { active: boolean; status: string }
export const voice: Writable<VoiceStatus> = writable({ active: false, status: 'off' })
let voiceCtl: VoiceController | null = null
let _voiceActive = false        // a voice session is running (mic state)
let _suppressStream = false     // suppress stream folding while voice drives the thread,
                                // and through the stop→reload window (cleared only by openThread)
let _vitem: VoiceBubble | null = null
let _vrole: VoiceRole | null = null

// Buffer raw {type,data} events for the AG2 Inspector (bounded ring). The stream
// already delivers every AG2 event here — we just keep them so the inspector can
// reveal the live AG2 activity behind the UI (no extra server work).
const _INSPECT_CAP = 600
function _inspect(ev: WireEvent): void {
  inspectorEvents.update((buf) => {
    const next = buf.length >= _INSPECT_CAP ? buf.slice(buf.length - _INSPECT_CAP + 1) : buf
    return [...next, { ...ev, _t: Date.now(), _id: (next.at(-1)?._id || 0) + 1 }]
  })
}

export function openThread(kind: ThreadKind, id: string): void {
  closeThread()
  _suppressStream = false       // a fresh thread always folds its stream
  const session = _session
  const chat = kind === 'run' ? 'task-run:' + id : id
  _historyEvents = []; _buffer = []; _ready = false; _fold = new AguiState(); _interrupts = new Set()
  chatHistory.set({ chat, loading: true, loadingOlder: false, error: '', before: null, start: null })
  thread.set({ id, kind, chat, items: [], busy: false })
  inspectorEvents.set([])       // fresh inspector buffer per thread
  // Reset synchronously, so an unsent model choice can never leak from the chat we
  // just left onto this one. The composer's switcher fills it from the server.
  chatModel.set(openedChat(chat))

  watchChat(chat, session, true)

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

// Subscribe to the chat's events outside a run. Every (re)connect reloads the latest
// page when `reload` (a run's end re-subscribes without it: the run already showed them).
function watchChat(chat: string, session: number, reload: boolean): void {
  let first = reload
  watch = new ChatWatch(chat, {
    onOpen: () => {
      if (session !== _session) return
      if (!first) { first = true; return }   // later connects have missed events
      _connection++
      _ready = false; _buffer = []
      void loadLatest(chat, session)
    },
    onEvent: (event) => {
      if (session !== _session) return
      if (!_ready) _buffer.push(event)
      else apply(event)
    },
  })
}

// Fold the chat's latest page, then the live events that came in while it loaded.
async function loadLatest(chat: string, session: number): Promise<void> {
  const connection = _connection
  chatHistory.update(s => ({ ...s, loading: true, loadingOlder: false, error: '' }))
  try {
    const page = await api.chatEvents(chat)
    if (session !== _session || connection !== _connection) return
    _historyEvents = page.events as BaseEvent[]
    inspectorEvents.set([])
    _historyEvents.forEach(_inspectAgui)
    _fold = new AguiState()
    thread.update(t => ({ ...t, items: projectHistory(_historyEvents, t.items, _fold) }))
    chatHistory.set({ chat, loading: false, loadingOlder: false, error: '', before: page.before, start: page.start })
  } catch (error) {
    if (session !== _session || connection !== _connection) return
    chatHistory.update(s => ({ ...s, loading: false, error: errText(error) }))
  }
  _ready = true
  const buffered = _buffer
  _buffer = []
  for (const event of buffered) apply(event)
}

// Show an AG-UI event in the AG2 Inspector: an assistant event as itself, the rest by AG-UI type.
function _inspectAgui(event: BaseEvent): void {
  const ev = event as unknown as Record<string, any>
  _inspect(ev.type === 'CUSTOM' ? { type: ev.name, data: ev.value ?? {} } : { type: 'ag-ui.' + ev.type, data: ev })
}

// Fold one live event into the thread and track whether a run is in flight.
function apply(event: BaseEvent): void {
  const ev = event as unknown as Record<string, any>
  _inspectAgui(event)
  if (_suppressStream) return
  if (ev.type === 'CUSTOM' && String(ev.name).endsWith('.CardInstanceSaved') && typeof ev.value?.path === 'string') refreshFiles(ev.value.path)
  _historyEvents.push(event)
  const running = runningAfter(event)
  const wire = statusEvent(ev)
  thread.update((t) => {
    foldAgui(t.items, event, _fold)
    if (wire && t.kind === 'chat') {
      chats.update((rows) => rows.map((row) => {
        if (row.chat_id !== t.chat) return row
        const snapshot = chatEventStatus(row, wire, pendingChatDeadlines(t.items, row.turn_started_at))
        return snapshot ? mergeChatStatus(row, snapshot) : row
      }))
    }
    return { ...t, items: t.items, busy: running ?? t.busy }
  })
}

// The assistant event an AG-UI event carries, as the drawer's status reads it; an interrupt is an InquiryRaised.
function statusEvent(ev: Record<string, any>): WireEvent | null {
  if (ev.type === 'CUSTOM') return { type: ev.name, data: ev.value ?? {} }
  const interrupt = ev.type === 'RUN_FINISHED' ? ev.outcome?.interrupts?.[0] : undefined
  if (!interrupt) return null
  return { type: 'InquiryRaised', data: { created_at: (ev.timestamp ?? Date.now()) / 1000, inquiry_id: interrupt.id, expires_at: interrupt.metadata?.expires_at } }
}

// Run a turn, or answer the Inquiry that ended the last run. The subscription is closed for
// the run's length: the run carries the same events, so nothing is shown twice.
function startRun(input: RunInput): void {
  const t = get(thread)
  if (!t.chat) return
  const { chat } = t
  const session = _session
  watch?.close(); watch = null
  stopRun?.()
  thread.update((x) => ({ ...x, busy: true }))
  const finish = (): void => {
    if (session !== _session) return
    stopRun = null
    thread.update((x) => ({ ...x, busy: false }))
    if (!watch) watchChat(chat, session, false)
  }
  stopRun = runChat(chat, input, {
    onEvent: (event) => {
      if (session !== _session) return
      const ev = event as unknown as Record<string, any>
      for (const interrupt of ev.outcome?.interrupts ?? []) _interrupts.add(interrupt.id)
      apply(event)
    },
    onError: (error) => {
      if (session !== _session) return
      thread.update((x) => {
        x.items.push({ id: nextItemId(), kind: 'note', icon: 'x', text: errText(error), alert: true })
        return { ...x, items: x.items }
      })
      finish()
    },
    onEnd: finish,
  })
}

export function send(text: string, attachments: AttachmentPayload[] = []): boolean {
  const t = get(thread)
  if (!t.chat || (!text.trim() && !attachments.length)) return false
  // A model chosen before this chat existed rides the message that creates it; empty
  // for every other turn, whose override the server already resolves (ADR 0025).
  let model = ''
  const cm = get(chatModel)
  if (cm.chatId === t.chat) {
    const next = sentFirstMessage(cm)
    chatModel.set(next.state)
    model = next.model
  }
  if (t.busy) feed(t.chat, text, attachments, model)
  else startRun({ text, attachments, model })
  // Surface a brand-new chat in the drawer immediately — the chats list is
  // only persisted server-side once the first turn completes (which can take a
  // while). The drawer poll merges this until the server reports it for real.
  if (t.kind === 'chat' && text.trim()) {
    chats.update((list) =>
      list.some((s) => s.chat_id === t.chat)
        ? list.map((row) => row.chat_id === t.chat ? { ...row, status: 'running', turn_started_at: Date.now() / 1000, waiting_until: null, unread: false } : row)
        : [
            {
              chat_id: t.chat,
              preview: text.trim().slice(0, 80),
              updated: new Date().toISOString(),
              turns: 0,
              title: '',
              starred: false,
              status: 'running',
              status_version: '',
              status_updated: 0,
              turn_started_at: Date.now() / 1000,
              waiting_until: null,
              unread: false,
            },
            ...list,
          ]
    )
  }
  return true
}

// Steer the running turn: show the message queued at once, since the agent only echoes it
// when it next drains its inbox. A turn that has already ended runs it as a new one.
function feed(chat: string, text: string, attachments: AttachmentPayload[], model: string): void {
  let queued: ThreadItem | undefined
  if (text) thread.update((x) => { queueMessage(x.items, text); queued = x.items.at(-1); return { ...x, items: x.items } })
  api.feedChat(chat, text, attachments.length ? attachments : undefined)
    .then(({ fed }) => {
      if (fed || get(thread).chat !== chat) return
      thread.update((x) => ({ ...x, items: x.items.filter((i) => i !== queued) }))
      startRun({ text, attachments, model })
    })
    .catch((error) => {
      thread.update((x) => {
        x.items.push({ id: nextItemId(), kind: 'note', icon: 'x', text: errText(error), alert: true })
        return { ...x, items: x.items.filter((i) => i !== queued) }
      })
    })
}

// Stop the running turn. Stays busy until the server confirms — the run ends when
// the TurnCancelled event lands, not when we ask.
export function stop(): void {
  const { chat } = get(thread)
  if (chat) api.cancelChat(chat).catch((error) => console.warn('[chat] cancel failed', error))
}

// An Inquiry that ended the last run is answered by resuming it; any other one (a task's,
// or one raised outside a run) is answered over REST. Either way the first answer wins.
export function answer(inquiryId: string, text: string): void {
  if (_interrupts.delete(inquiryId)) {
    startRun({ interruptId: inquiryId, answer: text })
    return
  }
  api.answerInquiry(inquiryId, text)
    .catch(() => api.answerHitl(inquiryId, text))
    .catch((error) => console.warn('[chat] answer failed', error))
}

export function a2uiAction(message: unknown, state?: A2UISurfaceState): void {
  const { chat } = get(thread)
  if (chat) api.chatA2ui(chat, message, state).catch((error) => console.warn('[chat] a2ui action failed', error))
}

// The stable id/kind of the rated item plus the context the learner distils from.
export type FeedbackTarget = {
  targetKind: string
  targetId: string
  sentiment: string
  reason: string
  content?: string
  request?: string
}

// Send 👍/👎 + a (mandatory) reason on a generated item.
export function feedback({ targetKind, targetId, sentiment, reason, content = '', request = '' }: FeedbackTarget): void {
  const { chat } = get(thread)
  if (chat) api.rateChat(chat, { target_kind: targetKind, target_id: targetId, sentiment, reason, content, request }).catch((error) => console.warn('[chat] feedback failed', error))
}

// Retract a rating (toggle the 👍/👎 off). Clears only the visible thumb — the server
// emits FeedbackCleared and runs no learner, so learned memory is left untouched.
export function clearFeedback({ targetKind, targetId }: Pick<FeedbackTarget, 'targetKind' | 'targetId'>): void {
  const { chat } = get(thread)
  if (chat) api.clearChatRating(chat, { target_kind: targetKind, target_id: targetId }).catch((error) => console.warn('[chat] clear feedback failed', error))
}

export function closeThread(): void {
  _session++
  chatHistory.set({ chat: null, loading: false, loadingOlder: false, error: '', before: null, start: null })
  if (voiceCtl) { voiceCtl.stop(); voiceCtl = null }  // _voiceEnded's reload is guarded out on nav
  _voiceActive = false; _vitem = null; _vrole = null
  if (watch) { watch.close(); watch = null }
  if (stopRun) { stopRun(); stopRun = null }
  _ready = false; _buffer = []
  if (panelTimer) { clearInterval(panelTimer); panelTimer = null }
}

// Empty every profile-scoped store and bump profileEpoch, so a new profile starts
// clean without a page reload. Any store keyed to the active profile MUST be reset
// here; install-wide stores (llmConfigs, foldersStore, permissions) are left alone.
function resetProfileState(): void {
  closeThread()                                        // streams + panel timer + voice
  thread.set({ id: null, kind: 'chat', chat: '', items: [], busy: false })
  chatModel.set(NO_CHAT_MODEL)
  chats.set([])
  screenRows.set([])
  tasks.set([])
  runInfo.set(null)
  inquiries.set([])
  inspectorEvents.set([])
  viewer.set(null)                                     // drop the old profile's transient preview body
  profileEpoch.update((n) => n + 1)
}

// Switch the active profile in place (no reload → the Settings modal doesn't blink).
// Adopt the pid + accent before the route change so the new chat's streams scope
// to it; go('/') lands on the profile's home and preserves the hash. On any failure
// fall back to a full-page nav.
export function switchProfile(pid: string): void {
  if (!pid || pid === getActiveProfileId()) return
  try {
    resetProfileState()
    // The file preview rail addresses a file in the OLD profile's workspace and lives
    // in the URL's aside slot, which go() preserves — strip it so the switch closes it.
    if (get(route).aside?.kind === 'file') closeAside()
    setActiveProfileId(pid)
    const p = get(profiles).list.find((x) => x.id === pid)
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
function _setBusy(b: boolean): void { thread.update((t) => ({ ...t, busy: b })) }

function _voiceTranscript(role: VoiceRole, text: string, final: boolean): void {
  thread.update((t) => {
    if (final && role === 'user') {
      if (_vitem && _vrole === 'user') _vitem.text = text
      else t.items.push({ id: nextItemId(), kind: 'user', text, voice: true })
      _vitem = null; _vrole = null
      return { ...t, items: t.items, busy: true }   // thinking until the agent replies
    }
    if (_vitem && _vrole === role) { _vitem.text += text }
    else {
      const bubble: VoiceBubble = role === 'user'
        ? { id: nextItemId(), kind: 'user', text, voice: true }
        : { id: nextItemId(), kind: 'agent', text, voice: true }
      _vitem = bubble
      t.items.push(bubble); _vrole = role
    }
    return { ...t, items: t.items }
  })
}

export async function startVoice(): Promise<void> {
  const t = get(thread)
  if (!t.id || _voiceActive) return
  const epoch = get(profileEpoch)
  const query = '?chat=' + encodeURIComponent(t.chat)
  // capture at the active provider's native rate (Gemini 16 kHz / OpenAI 24 kHz)
  let inputRate = 16000
  try { inputRate = (await api.voices()).input_rate || 16000 } catch {}
  // A profile switch while the rate resolved would bind this session to the old chat.
  if (get(profileEpoch) !== epoch) return
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
function _voiceEnded(): void {
  voiceCtl = null; _voiceActive = false; _vitem = null; _vrole = null
  voice.set({ active: false, status: 'off' })
  thread.update((t) => ({ ...t, busy: false }))   // no lingering "thinking…" after hang-up
  setTimeout(() => { _suppressStream = false }, 2000)
}

export function stopVoice(): void {
  if (voiceCtl) voiceCtl.stop()   // → onState('off') → _voiceEnded (teardown + reload)
}

const _TERMINAL_TASK = new Set(['completed', 'failed', 'cancelled'])
let _markedSeen = false   // per-viewed-task latch: mark seen once, only after it finishes

async function loadRun(id: string): Promise<void> {
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
