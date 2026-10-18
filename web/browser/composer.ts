import { mount, unmount, tick } from 'svelte'
import { get } from 'svelte/store'
import Composer from '../src/components/Composer.svelte'
import { closeThread, openThread } from '../src/controller.ts'
import { profiles } from '../src/store.ts'
import { setActiveProfileId } from '../src/lib/profile.ts'
import { composerDrafts } from '../src/lib/composerDrafts.ts'
import { llmConfigs } from '../src/lib/llm.ts'
import '../src/design/styles.css'
import '../src/app.css'

const profile = new URL(location.href).searchParams.get('profile')
if (!profile) throw new Error('Pass ?profile=<running profile id> to the fixture URL')
setActiveProfileId(profile)
profiles.update(value => ({ ...value, activeId: profile }))
const target = document.querySelector<HTMLElement>('#fixtures')!
const results: { name: string; failures: string[] }[] = []
const outgoing: { text?: string; attachments?: { name: string; data: string }[] }[] = []

// The protocol peer accepts messages locally; no agent turn is started.
class LocalSocket {
  static OPEN = 1
  readyState = 1
  onopen?: () => void
  onmessage?: (event: { data: string }) => void
  constructor() {
    queueMicrotask(() => {
      this.onopen?.()
      this.onmessage?.({ data: JSON.stringify({ type: 'ready', busy: false }) })
    })
  }
  send(raw: string) {
    outgoing.push(JSON.parse(raw))
    this.onmessage?.({ data: JSON.stringify({ type: 'turn_end', role: 'agent' }) })
  }
  close() { this.readyState = 3 }
}
Object.defineProperty(window, 'WebSocket', { value: LocalSocket, configurable: true })

async function settle() {
  await tick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
}
async function until(predicate: () => boolean) {
  for (let i = 0; i < 100 && !predicate(); i++) await new Promise(resolve => setTimeout(resolve, 50))
  if (!predicate()) throw new Error('Composer fixture did not become ready')
}
function check(name: string, condition: boolean) {
  results.push({ name, failures: condition ? [] : ['Composer draft was lost or leaked'] })
}
function input(value: string) {
  const textarea = target.querySelector<HTMLTextAreaElement>('textarea')!
  textarea.value = value
  textarea.dispatchEvent(new InputEvent('input', { bubbles: true }))
  return textarea
}

openThread('chat', 'composer-first')
let component = mount(Composer, { target })
await settle()
await until(() => get(llmConfigs).loaded)
const owner = composerDrafts.forThread(profile, 'chat', 'composer-first')
input('@ag2-pr')
await settle()
target.querySelector('textarea')!.dispatchEvent(new KeyboardEvent('keyup', { key: 'r', bubbles: true }))
await until(() => !!target.querySelector('.atrow'))
target.querySelector('.atrow')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
await settle()
const reference = get(owner).refs[0]
check('workspace reference picked through the real picker', !!reference && !!target.querySelector('.cinput-hl mark'))
const text = `${'A multiline draft to retain.\n'.repeat(35)}${reference.label}`
const textarea = input(text)
await settle()
textarea.setSelectionRange(12, 24, 'backward')
textarea.dispatchEvent(new Event('select', { bubbles: true }))
textarea.scrollTop = 100
textarea.dispatchEvent(new Event('scroll'))
const saved = { height: textarea.style.height, scrollTop: textarea.scrollTop }
const files = new DataTransfer()
files.items.add(new File([new Uint8Array(1024 * 1024).fill(65)], 'draft.txt', { type: 'text/plain' }))
const fileInput = target.querySelector<HTMLInputElement>('input[type=file]')!
fileInput.files = files.files
fileInput.dispatchEvent(new Event('change', { bubbles: true }))
await unmount(component)
openThread('chat', 'composer-second')
component = mount(Composer, { target })
await settle()
input('Another chat draft')
await until(() => get(owner).pending.length === 1)
check('file encoding finishes in its original unmounted draft', !target.querySelector('.pending') && atob(get(owner).pending[0].payload.data).length === 1024 * 1024)
await unmount(component)
openThread('chat', 'composer-first')
component = mount(Composer, { target })
await settle()
const restored = target.querySelector<HTMLTextAreaElement>('textarea')!
check('text, file and reference survive remount', restored.value === text && !!target.querySelector('.pending')?.textContent?.includes('draft.txt') && !!target.querySelector('.cinput-hl mark'))
check('selection, direction, scroll and height survive remount', restored.selectionStart === 12 && restored.selectionEnd === 24 && restored.selectionDirection === 'backward' && restored.scrollTop === saved.scrollTop && restored.style.height === saved.height)
openThread('chat', 'composer-second')
await settle()
check('same component switches to its own chat draft', target.querySelector<HTMLTextAreaElement>('textarea')!.value === 'Another chat draft' && !target.querySelector('.pending'))
openThread('chat', 'composer-first')
await settle()
profiles.update(value => ({ ...value, activeId: 'another-profile' }))
await settle()
check('same chat id in another profile starts separately', target.querySelector<HTMLTextAreaElement>('textarea')!.value === '' && !target.querySelector('.pending'))
profiles.update(value => ({ ...value, activeId: profile }))
await settle()
check('returning to the profile restores the complete draft', target.querySelector<HTMLTextAreaElement>('textarea')!.value === text && !!target.querySelector('.pending'))
closeThread()
target.querySelector<HTMLButtonElement>('.csend')!.click()
await settle()
check('an unavailable stream does not clear the draft', target.querySelector<HTMLTextAreaElement>('textarea')!.value === text && !!target.querySelector('.pending'))
openThread('chat', 'composer-first')
await settle()
const attachment = get(owner).pending[0].payload
target.querySelector<HTMLButtonElement>('.csend')!.click()
await settle()
const sent = outgoing.at(-1)
check('restored bytes and reference are delivered in the message', !!sent?.text?.includes(reference.path) && sent.attachments?.[0].name === 'draft.txt' && sent.attachments[0].data === attachment.data)
check('send clears only the submitted draft', target.querySelector<HTMLTextAreaElement>('textarea')!.value === '' && !target.querySelector('.pending') && get(owner).refs.length === 0 && get(composerDrafts.forThread(profile, 'chat', 'composer-second')).text === 'Another chat draft')
openThread('chat', 'composer-second')
await settle()
openThread('chat', 'composer-first')
await settle()
check('submitted text does not return on navigation', target.querySelector<HTMLTextAreaElement>('textarea')!.value === '' && !target.querySelector('.pending'))
closeThread()
Object.assign(window, { layoutResults: results })
