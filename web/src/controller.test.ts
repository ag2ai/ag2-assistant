import assert from 'node:assert/strict'
import { test } from 'node:test'
import { get } from 'svelte/store'
import { JSDOM } from 'jsdom'
import { installBrowserGlobals } from './testing/browserGlobals.ts'
import { runFinished, runStarted, say } from './testing/agui.ts'
import type { AguiEvent } from './schemas/agui.ts'

installBrowserGlobals()
Object.defineProperty(globalThis, 'document', { value: new JSDOM().window.document, configurable: true })
const { openThread, closeThread, send, chatHistory } = await import('./controller.ts')
const { thread } = await import('./store.ts')
const { setActiveProfileId } = await import('./lib/profile.ts')

async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 10))
  }
  assert.fail(JSON.stringify(get(thread).items))
}

test('a late initial history snapshot cannot erase a new completed turn', async () => {
  const originalFetch = globalThis.fetch
  const streams: ReadableStreamDefaultController<Uint8Array>[] = []
  let finishInitial: ((response: Response) => void) | undefined
  const emptyHistory = () => new Response(JSON.stringify({ events: [], before: null, start: null }), { headers: { 'Content-Type': 'application/json' } })
  globalThis.fetch = async (input, init) => {
    if (init?.method === 'POST') {
      const events = [runStarted(), ...say('user', 'u', 'hello', 1), ...say('assistant', 'a', 'finished reply', 2), runFinished()]
      return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (String(input).endsWith('/agui/events')) {
      return new Response(new ReadableStream<Uint8Array>({ start(controller) { streams.push(controller) } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (!finishInitial) return new Promise<Response>(resolve => { finishInitial = resolve })
    return emptyHistory()
  }
  try {
    setActiveProfileId('work')
    openThread('chat', 'first')
    await until(() => finishInitial !== undefined)
    send('hello')
    await until(() => get(thread).items.some(item => item.kind === 'agent' && item.text === 'finished reply'))
    assert.ok(finishInitial)
    finishInitial(emptyHistory())
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.deepEqual(get(thread).items.flatMap(item => item.kind === 'user' || item.kind === 'agent' ? [item.text] : []), ['hello', 'finished reply'])
  } finally {
    closeThread()
    for (const controller of streams) controller.close()
    globalThis.fetch = originalFetch
    setActiveProfileId(null)
  }
})

for (const cleanEof of [false, true]) {
  test(`a POST ending with ${cleanEof ? 'early EOF' : 'a network error'} reloads the completed server-owned reply`, async () => {
    const originalFetch = globalThis.fetch
    let history: AguiEvent[] = []
    let running: ReadableStreamDefaultController<Uint8Array> | undefined
    const streams: ReadableStreamDefaultController<Uint8Array>[] = []
    const encode = (events: AguiEvent[]) => new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''))
    globalThis.fetch = async (input, init) => {
      const path = String(input)
      if (path.endsWith('/agui/events') || init?.method === 'POST') {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            streams.push(controller)
            if (init?.method !== 'POST') return
            running = controller
            controller.enqueue(encode([runStarted(), ...say('user', 'u', 'hello', 1), ...say('assistant', 'a', 'prefix:', 2).slice(0, 2)]))
          },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return new Response(JSON.stringify({ events: history, before: null, start: null }), { headers: { 'Content-Type': 'application/json' } })
    }
    try {
      setActiveProfileId('work')
      openThread('chat', 'c')
      await until(() => !get(chatHistory).loading)
      send('hello')
      await until(() => get(thread).items.some(item => item.kind === 'agent' && item.text === 'prefix:'))
      history = [...say('user', 'u', 'hello', 1), ...say('assistant', 'a', 'prefix:done', 3)]
      assert.ok(running)
      if (cleanEof) running.close()
      else running.error(new Error('connection dropped'))
      await until(() => get(thread).items.some(item => item.kind === 'agent' && item.text === 'prefix:done'))
      assert.equal(get(thread).items.filter(item => item.kind === 'agent').length, 1)
      assert.equal(get(thread).busy, false)
    } finally {
      closeThread()
      for (const controller of streams) {
        try { controller.close() } catch { /* a failed stream is already closed */ }
      }
      globalThis.fetch = originalFetch
      setActiveProfileId(null)
    }
  })
}
