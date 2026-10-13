import { mount, tick } from 'svelte'
import Thread from '../src/components/Thread.svelte'
import { chats, thread } from '../src/store.ts'
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

reset()
emit(request)
const update: WireEvent = { ...tools, data: { calls: [{ name: 'update_file' }] } }
const preview: WireEvent = { ...tools, data: { calls: [{ name: 'run_skill_script', arguments: { name: 'screens', script: 'preview' } }] } }
const rounds: WireEvent[] = []
for (const batch of [update, update, update, preview, preview]) {
  rounds.push({ ...response(''), data: { tool_calls: batch.data } }, batch, { type: 'ag2.events.ToolResult', data: {} })
}
for (const event of rounds) emit(event)
await check('grouped tools stay active', true)
function checkTools(name: string) {
  const failures: string[] = []
  const chips = [...target.querySelectorAll('.tools .chip')].map(node => node.textContent?.trim())
  if (JSON.stringify(chips) !== JSON.stringify(['update file ×3', 'run skill script ×2'])) failures.push(`Wrong grouped labels: ${chips}`)
  if (target.querySelectorAll('.tools').length !== 1) failures.push('Repeated tool rounds split into separate rows')
  if (target.querySelector('.toolcards .tcskill')) failures.push('Skill details remain in the main timeline')
  if (target.querySelectorAll('[aria-label="run skill script runs"] li').length !== 1 || target.querySelector('[aria-label="run skill script runs"] .run-count')?.textContent?.trim() !== '×2') failures.push('Grouped previews lost counted details')
  results.push({ name, failures })
}
checkTools('live counted tool labels')
const grouped: ThreadItem[] = []
for (const event of [request, ...rounds]) foldEvent(grouped, event)
thread.update(state => ({ ...state, items: grouped, busy: isBusy(grouped) }))
await check('replayed grouped tools stay active', true)
checkTools('replayed counted tool labels')
emit(response('Updated'))
await check('grouped tools finish', false)

reset()
emit(request)
let previewCount = 0
const append = (batch: WireEvent) => {
  emit({ ...response(''), data: { tool_calls: batch.data } })
  emit(batch)
}
for (const batch of [preview, preview, update, preview, update, preview, preview]) {
  append(batch)
  if (batch === preview) previewCount++
  if (previewCount === 3 && batch === preview) emit({ type: 'assistant.events.ObserverAlert', data: { message: 'Many calls' } })
  await check(`interleaved tools/${previewCount}`, true)
  const chips = [...target.querySelectorAll('.tool-trigger')].filter(node => node.textContent?.includes('run skill script'))
  const failures: string[] = []
  if (chips.length !== 1 || !chips[0].textContent?.includes(previewCount > 1 ? `×${previewCount}` : 'run skill script')) failures.push('Live counter did not update')
  results.push({ name: `live counter/${previewCount}`, failures })
}
const trigger = [...target.querySelectorAll<HTMLButtonElement>('.tool-trigger')].find(node => node.textContent?.includes('run skill script'))!
trigger.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
await check('hover keeps activity visible', true)
let popup = target.querySelector<HTMLDivElement>('.tool-runs:popover-open')!
const popupFailures: string[] = []
if (!popup || popup.querySelectorAll('li').length !== 1 || popup.querySelector('.run-count')?.textContent?.trim() !== '×5') popupFailures.push('Hover did not group all five calls')
if (popup && [...popup.querySelectorAll('li')].some(node => !node.textContent?.includes('screens · preview'))) popupFailures.push('Hover lost skill/script details')
results.push({ name: 'hover full run list', failures: popupFailures })
trigger.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }))
popup.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
await new Promise(resolve => setTimeout(resolve, 240))
results.push({ name: 'hover reaches the list', failures: popup.matches(':popover-open') ? [] : ['Popover closed while entering it'] })
popup.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }))
await new Promise(resolve => setTimeout(resolve, 240))
results.push({ name: 'hover leaves the list', failures: popup.matches(':popover-open') ? ['Popover did not close'] : [] })

trigger.focus()
await check('keyboard focus opens runs', true)
popup = target.querySelector<HTMLDivElement>('.tool-runs:popover-open')!
popup.focus(); popup.hidePopover()
await check('keyboard dismissal', true)
results.push({ name: 'focus restored after dismissal', failures: document.activeElement === trigger && !popup.matches(':popover-open') ? [] : ['Focus restore reopened the popover'] })
trigger.blur(); trigger.click()
await check('tap opens runs', true)
results.push({ name: 'tap pins the list', failures: popup.matches(':popover-open') ? [] : ['Tap did not open list'] })
trigger.click()
await check('second tap closes runs', true)

for (let index = 0; index < 50; index++) append({ ...preview, data: { calls: [{ name: 'run_skill_script', arguments: { name: 'screens', script: 'preview', args: { path: `screen-${index}.yaml` } } }] } })
trigger.click()
await check('long run list', true)
popup = target.querySelector<HTMLDivElement>('.tool-runs:popover-open')!
const longFailures: string[] = []
const bounds = popup.getBoundingClientRect()
if (bounds.left < 0 || bounds.right > window.innerWidth || bounds.top < 0 || bounds.bottom > window.innerHeight) longFailures.push('Popover escapes viewport')
if (popup.querySelectorAll('li').length !== 51 || popup.querySelector('.run-count')?.textContent?.trim() !== '×5' || !popup.querySelector('h2')?.textContent?.includes('×55')) longFailures.push('Run list lost distinct entries or total count')
popup.scrollTop = popup.scrollHeight
if (popup.scrollTop <= 0 || popup.querySelector('li:last-child')!.getBoundingClientRect().bottom > bounds.bottom + 1) longFailures.push('Last call cannot be reached')
results.push({ name: 'complete scrollable run list', failures: longFailures })
trigger.click()
await check('long list dismissed', true)
emit(response('Done'))
await check('aggregated phase finished', false)

reset()
emit(request)
const shell = `python3 -c "import json; from pathlib import Path; data=json.loads(Path('/example/learning/reports/weekly-metrics.json').read_text()); print(data['weeks'][-12:]); print('<img src=x onerror=alert(1)>')"`
const code = Array.from({ length: 35 }, (_, index) => `print('Week ${index + 1}: <img src=x onerror=alert(1)>')`).join('\n') + '\n'
append({ ...tools, data: { calls: [
  { name: 'run_shell_command', arguments: { command: shell } },
  { name: 'run_shell_command', arguments: { command: 'pwd' } },
  { name: 'run_shell_command', arguments: { command: shell } },
  { name: 'run_code', arguments: { code, language: 'python' } },
  { name: 'run_code', arguments: JSON.stringify({ code, language: 'python' }) },
  { name: 'run_code', arguments: { code, language: 'bash' } },
] } })
await check('execution tools retain activity', true)
for (const [label, source] of [['run shell command', shell], ['run code', code]]) {
  const executionTrigger = [...target.querySelectorAll<HTMLButtonElement>('.tool-trigger')].find(node => node.textContent?.trim().startsWith(label))!
  executionTrigger.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
  await tick()
  await new Promise(resolve => setTimeout(resolve, 100))
  const executionPopup = target.querySelector<HTMLDivElement>('.tool-runs:popover-open')!
  const entries = [...executionPopup.querySelectorAll<HTMLElement>('.execution-entry')]
  results.push({ name: `${label}/compact previews and grouped counts`, failures: entries.length === 2 && !executionPopup.querySelector('.execution-source') && entries[0].querySelector('.run-count')?.textContent?.trim() === '×2' && entries.every(entry => {
    const preview = entry.querySelector<HTMLElement>('.execution-preview')!
    const toggle = entry.querySelector('.detail-toggle')!
    return preview.getBoundingClientRect().height <= Number.parseFloat(getComputedStyle(preview).lineHeight) + 1 && !preview.textContent?.includes('\n') && toggle.getAttribute('aria-expanded') === 'false' && toggle.getAttribute('aria-label') === 'Show details' && !!toggle.querySelector('svg') && getComputedStyle(toggle.querySelector('svg')!).transform === 'none' && !toggle.textContent?.trim()
  }) ? [] : ['Execution source is not compact, grouped or collapsed initially'] })
  const toggle = entries[0].querySelector<HTMLButtonElement>('.detail-toggle')!
  toggle.click()
  await tick()
  await new Promise(resolve => setTimeout(resolve, 100))
  const full = entries[0].querySelector<HTMLElement>('.execution-source')!
  const expandedBounds = executionPopup.getBoundingClientRect()
  results.push({ name: `${label}/complete source expands inside the popover`, failures: full?.textContent === source && full.id === toggle.getAttribute('aria-controls') && toggle.getAttribute('aria-expanded') === 'true' && toggle.getAttribute('aria-label') === 'Hide details' && getComputedStyle(toggle.querySelector('svg')!).transform === 'matrix(-1, 0, 0, -1, 0, 0)' && !entries[1].querySelector('.execution-source') && !executionPopup.querySelector('img') && expandedBounds.left >= 0 && expandedBounds.right <= window.innerWidth && expandedBounds.top >= 0 && expandedBounds.bottom <= window.innerHeight ? [] : ['Complete source, safe rendering, independent expansion or containment failed'] })
  if (label === 'run code') {
    full.scrollTop = full.scrollHeight
    results.push({ name: 'long code remains scrollable to its final line', failures: full.scrollTop > 0 && full.getBoundingClientRect().height <= 220 && entries[0].querySelector('.execution-language')?.textContent === 'python' ? [] : ['Code is not scrollable or its language is missing'] })
  }
  toggle.click()
  await tick()
  results.push({ name: `${label}/details collapse without closing the run list`, failures: !entries[0].querySelector('.execution-source') && !!entries[0].querySelector('.execution-preview') && toggle.getAttribute('aria-label') === 'Show details' && executionPopup.matches(':popover-open') ? [] : ['Details did not collapse independently'] })
  toggle.dispatchEvent(new PointerEvent('pointerdown', { pointerType: 'mouse', bubbles: true }))
  toggle.focus()
  toggle.click()
  await tick()
  executionTrigger.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }))
  executionPopup.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }))
  await new Promise(resolve => setTimeout(resolve, 240))
  results.push({ name: `${label}/pointer focus does not prevent hover dismissal`, failures: executionPopup.matches(':popover-open') ? ['Menu stayed open after leaving a pointer-focused chevron'] : [] })
  executionTrigger.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
  await tick()
  results.push({ name: `${label}/reopening starts collapsed`, failures: !executionPopup.querySelector('.execution-source') && executionPopup.querySelector('.detail-toggle')?.getAttribute('aria-expanded') === 'false' ? [] : ['Expanded details survived closing the menu'] })
  executionTrigger.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }))
  executionPopup.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }))
  await new Promise(resolve => setTimeout(resolve, 240))
  await tick()
}

reset()
emit(request)
append({ ...tools, data: { created_at: 1791711720, calls: [{ name: 'write_file', arguments: { path: 'report.csv' } }] } })
await check('file controls remain accessible', true)
const fileTrigger = [...target.querySelectorAll<HTMLButtonElement>('.tool-trigger')].find(node => node.textContent?.includes('write file'))!
fileTrigger.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
await check('hover exposes file actions', true)
const filePopup = target.querySelector<HTMLDivElement>('.tool-runs:popover-open')!
results.push({ name: 'filename opens the file', failures: filePopup?.querySelector('button.file-name')?.textContent === 'report.csv' && filePopup.querySelector('[aria-label="Open file report.csv"]') && filePopup.querySelector('[aria-label="Download file report.csv"]') ? [] : ['Filename or download action disappeared'] })
const fileButtons = [...filePopup.querySelectorAll<HTMLButtonElement>('.file-entry .iconbtn')]
const fileNameBounds = filePopup.querySelector('.file-name')!.getBoundingClientRect()
results.push({ name: 'only download stays an icon beside the filename', failures: fileButtons.length === 1 && fileButtons[0].getAttribute('aria-label') === 'Download file report.csv' && !fileButtons[0].textContent?.trim() && fileButtons[0].querySelector('svg') && fileButtons[0].title && fileButtons[0].getBoundingClientRect().left >= fileNameBounds.right ? [] : ['Separate preview icon remains or download moved'] })
const fileTime = filePopup.querySelector('time')!
results.push({ name: 'call time replaces numbering in the left column', failures: !filePopup.querySelector('.run-index') && fileTime.getBoundingClientRect().right < fileNameBounds.left && !filePopup.querySelector('.run-entry time') ? [] : ['Time remains below the file or numbering remains'] })
results.push({ name: 'file artifacts have one timeline label', failures: target.querySelector('.toolcards') || target.querySelectorAll('.tools .chip').length !== 1 ? ['Duplicate file badge remains'] : [] })
filePopup.hidePopover()
append({ ...tools, data: { calls: [{ name: 'load_skill', arguments: { name: '<img src=x onerror=alert(1)>' } }] } })
await check('run details render as text', true)
results.push({ name: 'safe run details', failures: target.querySelector('.tool-runs img') ? ['Run details render raw HTML'] : [] })
results.push({ name: 'run lists have no close button', failures: target.querySelector('.runs-header button') ? ['Redundant close button remains'] : [] })
append({ ...tools, data: { calls: [{ name: 'remember' }, { name: 'remember' }] } })
await check('tools with no details keep their counter', true)
const rememberChip = [...target.querySelectorAll('.tools .chip')].find(node => node.textContent?.trim() === 'remember ×2')
results.push({ name: 'no empty or repeated-name popover', failures: rememberChip?.tagName === 'SPAN' && !target.querySelector('[aria-label="remember runs"]') ? [] : ['Tool without meaningful details still opens a popover'] })
reset()
emit(request)
append({ ...tools, data: { calls: [{ name: 'read_file', arguments: { path: '~/Desktop/english/plan.md' } }] } })
await check('read file has file actions', true)
const readTrigger = [...target.querySelectorAll<HTMLButtonElement>('.tool-trigger')].find(node => node.textContent?.includes('read file'))!
readTrigger.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
await check('read file hover', true)
const readPopup = target.querySelector('.tool-runs:popover-open')!
results.push({ name: 'read filenames open previews and retain download', failures: readPopup.querySelector('.file-name')?.textContent === '~/Desktop/english/plan.md' && readPopup.querySelector('[aria-label="Download file plan.md"]') ? [] : ['Read file has no preview or download'] })
reset()
emit(request)
append({ ...tools, data: { calls: [{ name: 'list_folder', arguments: { path: '/home/user/english/reports' } }] } })
await check('list folder has a directory target', true)
target.querySelector<HTMLButtonElement>('.tool-trigger')!.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
await check('list folder hover', true)
results.push({ name: 'folder path is a navigation action', failures: target.querySelector('.tool-runs:popover-open .folder-name')?.getAttribute('aria-label') === 'Open folder /home/user/english/reports' ? [] : ['Directory cannot be opened'] })
chats.set([{ chat_id: 'web-history', title: 'English study plan', preview: 'Weekly goals', turns: 4, updated: '2026-10-11T12:00:00Z', starred: false, status: null, status_version: '', status_updated: 0, turn_started_at: 0, waiting_until: null, unread: false }])
reset()
emit(request)
append({ ...tools, data: { calls: [{ name: 'read_chat', arguments: { chat_id: 'web-history' } }, { name: 'read_chat', arguments: { chat_id: 'web-deleted' } }] } })
await check('read chat has conversation details', true)
target.querySelector<HTMLButtonElement>('.tool-trigger')!.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }))
await check('read chat hover', true)
const chatPopup = target.querySelector('.tool-runs:popover-open')!
results.push({ name: 'read chat shows clickable title and identifier with unavailable-chat fallback', failures: chatPopup.querySelector('[aria-label="Open chat English study plan"]') && chatPopup.querySelector('.chat-id')?.textContent === 'web-history' && chatPopup.querySelector('[aria-label="Open chat web-deleted"]') ? [] : ['Conversation title, identifier or fallback missing'] })
chatPopup.querySelector<HTMLButtonElement>('.chat-name')!.click()
await tick()
results.push({ name: 'read chat opens the named conversation and dismisses the run list', failures: location.pathname.endsWith('/c/web-history') && !target.querySelector('.tool-runs:popover-open') ? [] : ['Conversation navigation failed or popover remains open'] })
reset()
emit(request)
emit(response('A reply aligned with the input.'))
for (const width of [360, 620, 780, 1000]) {
  target.style.width = `${width}px`
  await tick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  const reply = target.querySelector<HTMLElement>('.msg.agent .bubble')!.getBoundingClientRect()
  const input = target.querySelector<HTMLElement>('.inputbox')!.getBoundingClientRect()
  const failures: string[] = []
  if (Math.abs(reply.left - input.left) > 1 || Math.abs(reply.right - input.right) > 1) failures.push('Messages and input use different column bounds')
  results.push({ name: `chat column/${width}`, failures })
}
target.style.width = ''
Object.assign(window, { layoutResults: results })
