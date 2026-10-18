// A receive-only WebSocket to /api/p/{pid}/card-sources/stream: Card source events as
// {event:{type,data}}, preceded by a `ready` frame on every (re)connect.

import { api as P, onProfileGone } from '../lib/profile.ts'
import { EventFrame, ReadyFrame, type WireEvent } from '../schemas/events.ts'

const RECONNECT_MS = 1500

export type CardSourceHandlers = {
  onEvent?: (event: WireEvent) => void
  onReady?: () => void
}

// Pure frame decoding — malformed or unrecognised frames read as null.
export function readFrame(raw: string): EventFrame | ReadyFrame | null {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }
  const event = EventFrame.safeParse(value)
  if (event.success) return event.data
  const ready = ReadyFrame.safeParse(value)
  return ready.success ? ready.data : null
}

export class CardSourceSocket {
  private ws: WebSocket | null = null
  private closed = false
  private readonly handlers: CardSourceHandlers

  constructor(handlers: CardSourceHandlers = {}) {
    this.handlers = handlers
  }

  connect(): this {
    if (this.closed) return this
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}${P('/card-sources/stream')}`)
    this.ws.onmessage = (e: MessageEvent) => {
      const frame = readFrame(String(e.data))
      if (!frame) return
      if ('event' in frame) this.handlers.onEvent?.(frame.event)
      else if (frame.type === 'ready') this.handlers.onReady?.()
    }
    this.ws.onclose = (e: CloseEvent) => {
      if (this.closed) return
      // Profile archived (4001) / unknown or gone (4404/4410): re-resolve, don't retry.
      if (e.code === 4001 || e.code === 4404 || e.code === 4410) {
        this.closed = true
        onProfileGone('ws ' + e.code)
        return
      }
      setTimeout(() => this.connect(), RECONNECT_MS)
    }
    return this
  }

  close(): void {
    this.closed = true
    try { this.ws?.close() } catch { /* already closed */ }
  }
}
