import { mount, tick } from 'svelte'
import { get } from 'svelte/store'
import Thread from '../src/components/Thread.svelte'
import { openThread, closeThread, chatHistory, loadOlderMessages } from '../src/controller.ts'
import { thread } from '../src/store.ts'
import { setActiveProfileId } from '../src/lib/profile.ts'
import type { WireEvent } from '../src/schemas/events.ts'
import '../src/design/styles.css'
import '../src/app.css'

const profile = new URL(location.href).searchParams.get('profile')
if (!profile) throw new Error('Pass ?profile=<running profile id>')
setActiveProfileId(profile)
const target = document.querySelector<HTMLElement>('#fixtures')!
const results: { name: string; failures: string[] }[] = []
const request = (index: number): WireEvent => ({ type: 'ModelRequest', data: { created_at: index * 2 + 1, parts: [{ __event__: 'TextInput', content: `Question ${index}: ` + 'A readable historical question. '.repeat(5) }] } })
const response = (index: number): WireEvent => ({ type: 'ModelResponse', data: { created_at: index * 2 + 2, message: { content: `Answer ${index}: ` + 'The conversation continues with a detailed answer. '.repeat(10) } } })
let events: WireEvent[] = []
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
async function settle() { await tick(); await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) }
function check(name: string, ok: boolean, detail = 'Unexpected history or scroll state') { results.push({ name, failures: ok ? [] : [detail] }) }

class HistorySocket {
  static readonly OPEN = 1
  static last: HistorySocket
  static urls: string[] = []
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor(url: string) {
    HistorySocket.last = this; HistorySocket.urls.push(url)
    queueMicrotask(() => {
      this.onopen?.()
      const params = new URL(url).searchParams
      const start = params.get('history_from')
      const index = start ? Number(start.slice(4)) : Math.max(0, events.length / 2 - 10)
      const current = params.get('chat') === 'pagination-fixture' ? events.slice(index * 2) : []
      for (const event of current) this.emit({ event })
      this.emit({ type: 'ready', busy: false, history: { before: index && current.length ? `turn${index}` : null, start: current.length ? `turn${index}` : null } })
    })
  }
  emit(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }) }
  send() {}
  close() {}
}
Object.defineProperty(window, 'WebSocket', { value: HistorySocket, configurable: true })
const fetchOriginal = window.fetch.bind(window)
let resolvePage: ((response: Response) => void) | null = null
let pendingBefore = ''
let pageRequests = 0
window.fetch = (input, init) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  if (!url.includes('/chats/pagination-fixture/events?')) return fetchOriginal(input, init)
  pageRequests++
  pendingBefore = new URL(url, location.origin).searchParams.get('before') ?? ''
  return new Promise<Response>(resolve => { resolvePage = resolve })
}
function completePage(fail = false) {
  const end = Number(pendingBefore.slice(4)), start = Math.max(0, end - 10)
  const body = fail ? { detail: 'Try again' } : { events: events.slice(start * 2, end * 2), before: start ? `turn${start}` : null, start: `turn${start}` }
  resolvePage?.(new Response(JSON.stringify(body), { status: fail ? 503 : 200, headers: { 'content-type': 'application/json' } }))
  resolvePage = null
}
function anchor(el: HTMLElement) {
  return [...el.querySelectorAll<HTMLElement>('[data-thread-item]')]
    .find(node => node.offsetHeight > 0 && node.getBoundingClientRect().bottom >= el.getBoundingClientRect().top)!
}

mount(Thread, { target })
for (const width of [1280, 360]) {
  target.style.width = `${width}px`; target.style.height = '780px'; target.style.display = 'flex'; target.style.flexDirection = 'column'; target.style.position = 'relative'
  events = Array.from({ length: 40 }, (_, index) => [request(index), response(index)]).flat()
  openThread('chat', 'pagination-fixture')
  await settle(); await delay(100)
  const el = target.querySelector<HTMLElement>('.thread')!
  check(`${width}/initial recent page`, get(thread).items.filter(item => item.kind === 'user').length === 10)
  check(`${width}/initial at latest`, el.scrollHeight - el.clientHeight - el.scrollTop < 80)
  const visible = get(thread).items.map(item => item.id)
  const requests = pageRequests
  el.scrollTop = 80; el.dispatchEvent(new Event('scroll')); await settle()
  const oldAnchor = anchor(el), top = oldAnchor.getBoundingClientRect().top
  check(`${width}/scroll requests one page`, get(chatHistory).loadingOlder && pageRequests === requests + 1)
  el.dispatchEvent(new Event('scroll')); await settle()
  check(`${width}/duplicate requests suppressed`, pageRequests === requests + 1)
  events.push(request(40), response(40)); HistorySocket.last.emit({ event: request(40) }); HistorySocket.last.emit({ event: response(40) }); await settle()
  completePage(); await settle()
  check(`${width}/prepend and live messages`, get(thread).items.filter(item => item.kind === 'user').length === 21)
  check(`${width}/mounted items preserved`, visible.every(id => get(thread).items.some(item => item.id === id)))
  check(`${width}/scroll anchor preserved`, oldAnchor.isConnected && Math.abs(oldAnchor.getBoundingClientRect().top - top) < 2, `${oldAnchor.getBoundingClientRect().top} != ${top}`)
  const ids = get(thread).items.map(item => item.id)
  HistorySocket.last.onclose?.({ code: 1006 }); await delay(1700); await settle()
  check(`${width}/reconnect retains loaded range`, get(thread).items.filter(item => item.kind === 'user').length === 21 && HistorySocket.urls.at(-1)?.includes('history_from=turn20') === true)
  check(`${width}/reconnect preserves mounted identities`, JSON.stringify(ids) === JSON.stringify(get(thread).items.map(item => item.id)))
  el.scrollTop = 80; el.dispatchEvent(new Event('scroll')); await settle(); completePage(true); await settle()
  check(`${width}/failure retains visible history`, get(thread).items.filter(item => item.kind === 'user').length === 21 && !!get(chatHistory).error && !get(chatHistory).loadingOlder)
  ;(target.querySelector('.history-load') as HTMLButtonElement).click(); await settle(); completePage(); await settle()
  check(`${width}/retry loads earlier page`, !get(chatHistory).error && get(thread).items.filter(item => item.kind === 'user').length === 31)
  el.scrollTop = 80; el.dispatchEvent(new Event('scroll')); await settle(); completePage(); await settle()
  check(`${width}/oldest page ends pagination`, get(thread).items.filter(item => item.kind === 'user').length === 41 && get(chatHistory).before === null && !target.querySelector('.history-load'))
  openThread('chat', 'pagination-fixture'); await settle()
  const loading = loadOlderMessages(); await settle()
  openThread('chat', 'empty-fixture'); await settle(); completePage(); await loading; await settle()
  check(`${width}/late page cannot replace another chat`, get(thread).chat === 'empty-fixture' && get(thread).items.length === 0)
  closeThread()
}
window.fetch = fetchOriginal
Object.assign(window, { layoutResults: results })
