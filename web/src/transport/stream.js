// One WebSocket to /api/p/{pid}/stream for a session. Receives the session's
// events ({event:{type,data}}) — replayed on connect, then live — and sends
// turns. The caller folds events into thread items via project.js.

import { api as P, onProfileGone } from '../lib/profile.js'

export class StreamClient {
  constructor(sessionId, { onEvent, onReady, onOpen, onTurnEnd, onQueued, onError } = {}) {
    this.sessionId = sessionId
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

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}${P('/stream?session=' + encodeURIComponent(this.sessionId))}`
    this.ws = new WebSocket(url)
    this.ws.onopen = () => { this.onOpen(); const q = this._queue; this._queue = []; q.forEach((o) => this._raw(o)) }
    this.ws.onmessage = (e) => {
      let m
      try { m = JSON.parse(e.data) } catch { return }
      if (m.event) this.onEvent(m.event)
      else if (m.type === 'ready') this.onReady(m)
      else if (m.type === 'turn_end') this.onTurnEnd(m)
      else if (m.type === 'queued') this.onQueued(m)
      else if (m.type === 'error') this.onError(m)
    }
    this.ws.onclose = (e) => {
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

  _raw(obj) { try { this.ws.send(JSON.stringify(obj)) } catch {} }
  // Send now if the socket is OPEN; otherwise queue and flush on (re)connect, so
  // a typed turn is never silently lost during the connect/reconnect window.
  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this._raw(obj)
    else this._queue.push(obj)
  }
  // A turn sent while one is already running is fed to it server-side (the agent
  // picks it up at its next step) — same frame either way.
  send(text, attachments) { this._send({ text, attachments }) }
  cancel() { this._send({ type: 'cancel' }) }
  answer(id, answer) { this._send({ type: 'answer', id, answer }) }
  feedback(payload) { this._send({ type: 'feedback', ...payload }) }
  clearFeedback(payload) { this._send({ type: 'feedback_clear', ...payload }) }
  close() { this._closed = true; try { this.ws && this.ws.close() } catch {} }
}
