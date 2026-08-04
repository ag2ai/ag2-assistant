// One WebSocket to /api/p/{pid}/stream for a chat. Receives the chat's
// events ({event:{type,data}}) — replayed on connect, then live — and sends
// turns. The caller folds events into thread items via project.ts.

import { api as P, onProfileGone } from '../lib/profile.ts'
import {
  ServerFrame,
  type AttachmentPayload,
  type ClientFrame,
  type ErrorFrame,
  type QueuedFrame,
  type ReadyFrame,
  type TurnEndFrame,
  type WireEvent,
} from '../schemas/events.ts'

// Pure frame decoding — malformed or unrecognised frames read as null.
export function readFrame(raw: string): ServerFrame | null {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }
  const result = ServerFrame.safeParse(value)
  return result.success ? result.data : null
}

export type StreamHandlers = {
  onEvent?: (event: WireEvent) => void
  onReady?: (frame: ReadyFrame) => void
  onOpen?: () => void
  onTurnEnd?: (frame: TurnEndFrame) => void
  onQueued?: (frame: QueuedFrame) => void
  onError?: (frame: ErrorFrame) => void
}

export class StreamClient {
  readonly chatId: string
  private readonly onEvent: (event: WireEvent) => void
  private readonly onReady: (frame: ReadyFrame) => void
  private readonly onQueued: (frame: QueuedFrame) => void
  private readonly onOpen: () => void
  private readonly onTurnEnd: (frame: TurnEndFrame) => void
  private readonly onError: (frame: ErrorFrame) => void
  private ws: WebSocket | null
  private _closed: boolean
  private _queue: ClientFrame[]

  constructor(
    chatId: string,
    { onEvent, onReady, onOpen, onTurnEnd, onQueued, onError }: StreamHandlers = {},
  ) {
    this.chatId = chatId
    this.onEvent = onEvent || (() => {})
    this.onReady = onReady || (() => {})
    // The server fed this message to the turn already running. It won't come back as an
    // event until the agent drains its inbox (a tool round away), so this is our cue to
    // show it as queued in the meantime.
    this.onQueued = onQueued || (() => {})
    // Fires on every (re)connect, BEFORE the server's replay events arrive. The
    // server replays the full history on each connect, so the caller uses this to
    // start a fresh replay buffer and avoid double-folding on reconnect.
    this.onOpen = onOpen || (() => {})
    this.onTurnEnd = onTurnEnd || (() => {})
    this.onError = onError || (() => {})
    this.ws = null
    this._closed = false
    this._queue = []   // outgoing messages sent while the socket wasn't OPEN
  }

  connect(): this {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}${P('/stream?chat=' + encodeURIComponent(this.chatId))}`
    this.ws = new WebSocket(url)
    this.ws.onopen = () => { this.onOpen(); const q = this._queue; this._queue = []; q.forEach((o) => this._raw(o)) }
    this.ws.onmessage = (e: MessageEvent) => {
      const frame = readFrame(String(e.data))
      if (!frame) return
      if ('event' in frame) { this.onEvent(frame.event); return }
      switch (frame.type) {
        case 'ready': this.onReady(frame); break
        case 'turn_end': this.onTurnEnd(frame); break
        case 'queued': this.onQueued(frame); break
        case 'error': this.onError(frame); break
      }
    }
    this.ws.onclose = (e: CloseEvent) => {
      if (this._closed) return
      // Profile archived (4001) / unknown or gone (4404/4410): don't retry
      // forever against a dead profile — re-resolve (§7 item 6).
      if (e && (e.code === 4001 || e.code === 4404 || e.code === 4410)) {
        this._closed = true
        onProfileGone('ws ' + e.code)
        return
      }
      setTimeout(() => this.connect(), 1500)
    }
    return this
  }

  private _raw(obj: ClientFrame): void { try { this.ws?.send(JSON.stringify(obj)) } catch {} }
  // Send now if the socket is OPEN; otherwise queue and flush on (re)connect, so
  // a typed turn is never silently lost during the connect/reconnect window.
  private _send(obj: ClientFrame): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this._raw(obj)
    else this._queue.push(obj)
  }
  // A turn sent while one is already running is fed to it server-side (the agent
  // picks it up at its next step) — same frame either way.
  send(text: string, attachments?: AttachmentPayload[]): void { this._send({ text, attachments }) }
  cancel(): void { this._send({ type: 'cancel' }) }
  answer(id: string, answer: string): void { this._send({ type: 'answer', id, answer }) }
  feedback(payload: Record<string, unknown>): void { this._send({ type: 'feedback', ...payload }) }
  clearFeedback(payload: Record<string, unknown>): void { this._send({ type: 'feedback_clear', ...payload }) }
  a2ui(message: unknown): void { this._send({ type: 'a2ui', message }) }
  close(): void { this._closed = true; try { this.ws?.close() } catch {} }
}
