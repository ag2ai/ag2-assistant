// One WebSocket to /api/stream for a session. Receives the session's events
// ({event:{type,data}}) — replayed on connect, then live — and sends turns.
// The caller folds events into thread items via project.js.

export class StreamClient {
  constructor(sessionId, { onEvent, onReady, onTurnEnd, onError } = {}) {
    this.sessionId = sessionId
    this.onEvent = onEvent || (() => {})
    this.onReady = onReady || (() => {})
    this.onTurnEnd = onTurnEnd || (() => {})
    this.onError = onError || (() => {})
    this.ws = null
    this._closed = false
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/api/stream?session=${encodeURIComponent(this.sessionId)}`
    this.ws = new WebSocket(url)
    this.ws.onmessage = (e) => {
      let m
      try { m = JSON.parse(e.data) } catch { return }
      if (m.event) this.onEvent(m.event)
      else if (m.type === 'ready') this.onReady(m)
      else if (m.type === 'turn_end') this.onTurnEnd(m)
      else if (m.type === 'error') this.onError(m)
    }
    this.ws.onclose = () => { if (!this._closed) setTimeout(() => this.connect(), 1500) }
    return this
  }

  _send(obj) { if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj)) }
  send(text, attachments) { this._send({ text, attachments }) }
  answer(id, answer) { this._send({ type: 'answer', id, answer }) }
  close() { this._closed = true; try { this.ws && this.ws.close() } catch {} }
}
