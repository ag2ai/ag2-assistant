import assert from 'node:assert/strict'

const [chrome, vite] = process.argv.slice(2)
if (!chrome || !vite) throw Error('Usage: npm --prefix web run test:layout -- <dedicated Chrome debug URL> <Vite URL>')
const targets = await (await fetch(`${chrome}/json/list`)).json()
const socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl)
await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }))
let seq = 0
const pending = new Map(), errors = []
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data)
  if (message.id) {
    const promise = pending.get(message.id)
    pending.delete(message.id)
    message.error ? promise.reject(message.error) : promise.resolve(message.result)
  } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails)
  else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') errors.push(message.params)
  else if (message.method === 'Network.responseReceived' && message.params.response.status >= 400) errors.push(message.params.response)
  else if (message.method === 'Network.loadingFailed' && !message.params.canceled) errors.push(message.params)
})
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq
  pending.set(id, { resolve, reject })
  socket.send(JSON.stringify({ id, method, params }))
})
try {
  await Promise.all([send('Runtime.enable'), send('Page.enable'), send('Network.enable')])
  await send('Network.setCacheDisabled', { cacheDisabled: true })
  await send('Page.navigate', { url: `${vite}/browser/layout.html` })
  let results
  for (let attempts = 0; attempts < 60 && !results; attempts++) {
    await new Promise(resolve => setTimeout(resolve, 250))
    const value = await send('Runtime.evaluate', { expression: 'window.layoutResults', returnByValue: true })
    results = value.result.value
    if (errors.length) break
  }
  assert.deepEqual(errors, [], 'Browser console and network errors')
  assert.ok(results?.length, 'Layout fixtures did not finish')
  const failures = results.filter(result => result.failures.length)
  assert.deepEqual(failures, [], 'A2UI containment regressions')
  console.log(`${results.length} layout fixtures passed in Chrome: aligned/nested tables stay contained; wide tables scroll to the last column.`)
} finally {
  socket.close()
}
