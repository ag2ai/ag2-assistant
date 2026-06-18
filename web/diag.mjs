// Headless smoke test: run the built bundle under jsdom to surface mount errors
// or infinite loops (run with a timeout). Not part of the app.
import { JSDOM } from 'jsdom'
import { readFileSync, readdirSync } from 'node:fs'

const dom = new JSDOM('<!doctype html><html><body><div id="app"></div></body></html>', {
  url: 'http://127.0.0.1:8800/app/c/diagtest',
  pretendToBeVisual: true,
})
const w = dom.window
global.window = w
global.document = w.document
global.location = w.location
global.history = w.history
for (const k of ['HTMLElement', 'Node', 'NodeFilter', 'MutationObserver', 'Element', 'Text',
  'Comment', 'DocumentFragment', 'Event', 'CustomEvent', 'getComputedStyle', 'CSSStyleSheet']) {
  if (w[k] !== undefined) global[k] = w[k]
}
global.requestAnimationFrame = () => 0
global.cancelAnimationFrame = (id) => clearTimeout(id)
global.WebSocket = class { constructor() {} send() {} close() {} addEventListener() {} set onmessage(v) {} set onclose(v) {} set onopen(v) {} set onerror(v) {} }
global.fetch = async () => ({ ok: true, json: async () => ({ sessions: [], tasks: [] }) })

process.on('uncaughtException', (e) => { console.error('UNCAUGHT:', e && (e.stack || e)); process.exit(2) })
process.on('unhandledRejection', (e) => { console.error('UNHANDLED:', e && (e.stack || e)); process.exit(3) })

const dir = new URL('../src/assistant/gateway/static/app/assets/', import.meta.url)
const js = readdirSync(dir).find((f) => f.endsWith('.js'))
console.log('loading bundle:', js)
await import(new URL(js, dir).href)
console.log('MOUNTED OK. #app length =', w.document.getElementById('app').innerHTML.length)
setTimeout(() => { console.log('still alive after 1s, app len =', w.document.getElementById('app').innerHTML.length); process.exit(0) }, 1000)
