// A Chat over AG-UI: a run (POST -> SSE, one Turn) and the subscription to everything the
// Chat does outside a run. Events come back as @ag-ui/client's validated BaseEvents.

import { runHttpRequest, transformHttpEventStream } from '@ag-ui/client'
import { AguiEvent } from '../schemas/agui.ts'
import { parse } from './validate.ts'
import { api as P, onProfileGone } from '../lib/profile.ts'
import { ApiError } from './http.ts'
import type { AttachmentPayload } from '../schemas/events.ts'

// A new Turn from the user, or the answer to the Inquiry that ended the previous run.
export type RunInput =
  | { text: string; attachments?: AttachmentPayload[]; model?: string }
  | { interruptId: string; answer: string }

export type Handlers = {
  onEvent: (event: AguiEvent) => void
  onOpen?: () => void
  onError?: (error: Error) => void
  onEnd?: () => void
}

const RECONNECT_MS = 1500

const uuid = (): string => crypto.randomUUID()

// The profile itself is gone (archived or unknown), not just this route.
function gone(status: number, body: unknown): boolean {
  const error = body !== null && typeof body === 'object' && 'error' in body ? body.error : undefined
  return status === 410 || (status === 404 && typeof error === 'string' && error.startsWith('unknown profile:'))
}

async function open(path: string, init: RequestInit): Promise<Response> {
  const response = await fetch(path, init)
  if (response.ok) return response
  let body: unknown = null
  try { body = await response.json() } catch { /* a non-JSON error keeps its status line */ }
  if (gone(response.status, body)) onProfileGone('sse ' + response.status)
  const detail = body !== null && typeof body === 'object'
    ? ('detail' in body ? body.detail : 'error' in body ? body.error : undefined) : undefined
  throw new ApiError(String(detail ?? `${init.method} ${path} -> ${response.status}`),
    response.status, body)
}

function stream(path: string, init: RequestInit, handlers: Handlers): () => void {
  const controller = new AbortController()
  const subscription = transformHttpEventStream(runHttpRequest(async () => {
    const response = await open(path, { ...init, signal: controller.signal })
    handlers.onOpen?.()
    return response
  })).subscribe({
    next: (event) => handlers.onEvent(parse(AguiEvent, event, 'AG-UI event')),
    error: (error) => { if (!controller.signal.aborted) handlers.onError?.(error instanceof Error ? error : new Error(String(error))) },
    complete: () => { if (!controller.signal.aborted) handlers.onEnd?.() },
  })
  return () => { controller.abort(); subscription.unsubscribe() }
}

// One run on a Chat. The returned function abandons the exchange; the Turn lives on server-side.
export function runChat(chat: string, input: RunInput, handlers: Handlers): () => void {
  const resume = 'interruptId' in input
    ? [{ interruptId: input.interruptId, status: 'resolved', payload: input.answer }]
    : undefined
  const body = {
    threadId: chat,
    runId: uuid(),
    state: {},
    messages: 'text' in input ? [{ id: uuid(), role: 'user', content: input.text }] : [],
    tools: [],
    context: [],
    forwardedProps: 'text' in input ? { attachments: input.attachments, model: input.model || undefined } : {},
    ...(resume ? { resume } : {}),
  }
  return stream(P('/chats/' + encodeURIComponent(chat) + '/agui'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
  }, handlers)
}

// The Chat's events outside a run, re-subscribed whenever the connection drops.
// `onOpen` fires on every (re)connect, before its events.
export class ChatWatch {
  private stop: (() => void) | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private closed = false
  private readonly chat: string
  private readonly handlers: Handlers

  constructor(chat: string, handlers: Handlers) {
    this.chat = chat
    this.handlers = handlers
    this.connect()
  }

  private connect(): void {
    if (this.closed) return
    const again = (): void => {
      this.stop?.()
      this.stop = null
      if (!this.closed) this.timer = setTimeout(() => this.connect(), RECONNECT_MS)
    }
    this.stop = stream(P('/chats/' + encodeURIComponent(this.chat) + '/agui/events'), {
      method: 'GET',
      headers: { Accept: 'text/event-stream' },
    }, {
      ...this.handlers,
      onError: (error) => { this.handlers.onError?.(error); again() },
      onEnd: again,
    })
  }

  close(): void {
    this.closed = true
    if (this.timer) clearTimeout(this.timer)
    this.stop?.()
    this.stop = null
  }
}
