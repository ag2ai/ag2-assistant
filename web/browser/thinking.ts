import { mount, tick } from 'svelte'
import Thread from '../src/components/Thread.svelte'
import { thread } from '../src/store.ts'
import { setActiveProfileId } from '../src/lib/profile.ts'
import { foldEvent, isBusy } from '../src/project.ts'
import type { ThreadItem, WireEvent } from '../src/schemas/events.ts'
import '../src/design/styles.css'
import '../src/app.css'

const profile = new URL(location.href).searchParams.get('profile')
if (!profile) throw new Error('Pass ?profile=<running profile id> to the fixture URL')
setActiveProfileId(profile)
const target = document.querySelector<HTMLElement>('#fixtures')!
const results: { name: string; failures: string[] }[] = []
const request: WireEvent = { type: 'ag2.events.ModelRequest', data: { parts: [{ __event__: 'ag2.events.TextInput', content: 'Plot the weekly data' }] } }
const tools: WireEvent = { type: 'ag2.events.ToolCallsEvent', data: { calls: [{ name: 'read_file', arguments: { path: 'weekly.json' } }] } }
const response = (content: string, calls = false): WireEvent => ({ type: 'ag2.events.ModelResponse', data: { message: { content }, tool_calls: { calls: calls ? tools.data.calls : [] } } })
const chunk = (content: string): WireEvent => ({ type: 'ag2.events.ModelMessageChunk', data: { content } })

function reset() {
  thread.set({ id: 'thinking-fixture', kind: 'chat', chat: '', items: [], busy: false })
}

function emit(event: WireEvent) {
  thread.update(state => {
    foldEvent(state.items, event)
    return { ...state, busy: isBusy(state.items) }
  })
}

async function check(name: string, thinking: boolean, compacting = false) {
  await tick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  const failures: string[] = []
  if (!!target.querySelector('[aria-label="Thinking"]') !== thinking) failures.push('Wrong Thinking visibility')
  const status = [...target.querySelectorAll('[role="status"]')].some(node => node.textContent?.includes('Compacting history'))
  if (status !== compacting) failures.push('Wrong compaction visibility')
  results.push({ name, failures })
}

reset()
mount(Thread, { target })
for (const content of ['I will read the data.', '']) {
  reset()
  const replay: WireEvent[] = [request, chunk(content), response(content, true), tools, { type: 'ag2.events.ToolResult', data: { content: 'Weekly data' } }]
  for (let i = 0; i < replay.length; i++) {
    emit(replay[i])
    await check(`${content || 'no text'}/${i}`, i !== 1)
  }
  await new Promise(resolve => setTimeout(resolve, 150))
  await check(`${content || 'no text'}/between rounds`, true)
  const items: ThreadItem[] = []
  for (const event of replay) foldEvent(items, event)
  thread.update(state => ({ ...state, items, busy: isBusy(items) }))
  await check(`${content || 'no text'}/reopen during tools`, true)
  emit(chunk('Here is the result.'))
  await check(`${content || 'no text'}/streaming result`, false)
  emit(response('Here is the result.'))
  await check(`${content || 'no text'}/final result`, false)
}

emit({ type: 'ag2.events.CompactionStarted', data: { agent: 'ag2-assistant' } })
await check('compaction has its own status', false, true)
emit({ type: 'ag2.events.CompactionCompleted', data: { agent: 'ag2-assistant' } })
await check('compaction completed', false)
for (const type of ['TurnCancelled', 'TurnFailed']) {
  reset()
  emit(request); emit(response('Reading…', true)); emit(tools)
  emit({ type: `assistant.events.${type}`, data: { reason: 'Stopped', error: 'Timed out' } })
  await check(type, false)
}
Object.assign(window, { layoutResults: results })
