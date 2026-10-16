import { test } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { marked } from 'marked'
import { bindMessageBlocks, MESSAGE_PREVIEW_LINES } from './messageBlocks.ts'

const code = (lines: number) => '```json\n' + Array.from({ length: lines }, (_, i) => `line ${i + 1}`).join('\n') + '\n```'

function message(text: string, streaming = false) {
  const doc = new JSDOM('<div class="thread"><div class="bubble"></div></div>').window.document
  const root = doc.querySelector<HTMLElement>('.bubble')!
  const state = new Map<string, boolean>()
  function render(markdown: string, busy = false) {
    root.innerHTML = marked.parse(markdown) as string
    bindMessageBlocks(root, { id: 'message-1', streaming: busy, state })
  }
  render(text, streaming)
  return { root, render }
}

test('plain prose and inline code stay readable without disclosure buttons', () => {
  const { root } = message('A short reply with `inline code`.')
  assert.equal(root.querySelector('button'), null)
  assert.equal(root.querySelector('code')?.textContent, 'inline code')
})

test('a long code block starts folded and can reveal its entire original content', () => {
  const { root } = message(code(MESSAGE_PREVIEW_LINES + 8))
  const control = root.querySelector<HTMLButtonElement>('button')!
  const content = root.querySelector<HTMLElement>('.message-code-content')!
  const original = root.querySelector('pre')!.textContent
  assert.equal(control.getAttribute('aria-expanded'), 'false')
  assert.equal(control.getAttribute('aria-controls'), content.id)
  assert.equal(content.classList.contains('is-collapsed'), true)
  assert.match(root.querySelector('.message-code-header')!.textContent!, /json · 20 lines/)
  control.click()
  assert.equal(control.getAttribute('aria-expanded'), 'true')
  assert.equal(content.classList.contains('is-collapsed'), false)
  assert.equal(root.querySelector('pre')!.textContent, original)
  control.click()
  assert.equal(control.getAttribute('aria-expanded'), 'false')
})

test('short code starts open and its collapse button hides the block', () => {
  const { root } = message(code(1))
  const control = root.querySelector<HTMLButtonElement>('button')!
  const content = root.querySelector<HTMLElement>('.message-code-content')!
  assert.equal(control.getAttribute('aria-expanded'), 'true')
  assert.match(root.querySelector('.message-code-header')!.textContent!, /json · 1 line/)
  control.click()
  assert.equal(content.hidden, true)
  control.click()
  assert.equal(content.hidden, false)
})

test('heading sections preserve nested content and leave following sections independent', () => {
  const { root } = message('Intro\n\n## First\n\nBody\n\n### Nested\n\n- Detail\n\n## Second\n\nConclusion')
  const sections = root.querySelectorAll<HTMLElement>('.message-section')
  assert.equal(sections.length, 3)
  const first = sections[0].querySelector<HTMLElement>('.message-section-content')!
  assert.match(first.textContent!, /Body/)
  assert.match(first.textContent!, /Nested/)
  assert.match(first.textContent!, /Detail/)
  assert.doesNotMatch(first.textContent!, /Conclusion/)
  sections[0].querySelector<HTMLButtonElement>('button')!.click()
  assert.equal(first.hidden, true)
  assert.equal(sections[2].querySelector<HTMLElement>('.message-section-content')!.hidden, false)
  assert.equal(root.firstElementChild!.textContent, 'Intro')
})

test('streaming code stays open and long code folds when the reply finishes', () => {
  const { root, render } = message(code(20), true)
  assert.equal(root.querySelector('button')!.getAttribute('aria-expanded'), 'true')
  render(code(24), true)
  assert.equal(root.querySelector('button')!.getAttribute('aria-expanded'), 'true')
  render(code(24))
  assert.equal(root.querySelector('button')!.getAttribute('aria-expanded'), 'false')
})

test('reader choices survive subsequent streaming renders and finalization', () => {
  const { root, render } = message('## Details\n\n' + code(20), true)
  root.querySelector<HTMLButtonElement>('.message-code-header button')!.click()
  root.querySelector<HTMLButtonElement>('.message-section-heading button')!.click()
  render('## Details\n\n' + code(24), true)
  assert.equal(root.querySelector('.message-code-header button')!.getAttribute('aria-expanded'), 'false')
  assert.equal(root.querySelector<HTMLElement>('.message-section-content')!.hidden, true)
  render('## Details\n\n' + code(24))
  assert.equal(root.querySelector('.message-code-header button')!.getAttribute('aria-expanded'), 'false')
  assert.equal(root.querySelector<HTMLElement>('.message-section-content')!.hidden, true)
  root.querySelector<HTMLButtonElement>('.message-code-header button')!.click()
  render('## Details\n\n' + code(30))
  assert.equal(root.querySelector('.message-code-header button')!.getAttribute('aria-expanded'), 'true')
})

test('empty headings have no disclosure and heading links retain their destinations', () => {
  const { root } = message('## [Reference](https://example.com)\n\nText\n\n## Empty')
  assert.equal(root.querySelectorAll('.message-section').length, 1)
  assert.equal(root.querySelector('h2 a')!.getAttribute('href'), 'https://example.com')
  assert.equal(root.lastElementChild!.tagName, 'H2')
  assert.equal(root.lastElementChild!.textContent, 'Empty')
})
