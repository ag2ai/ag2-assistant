import assert from 'node:assert/strict'
import { test } from 'node:test'
import { get } from 'svelte/store'
import { ComposerDrafts } from './composerDrafts.ts'
import { composeMessage } from './fileRefs.ts'

test('reopening a thread retains its text, attachment bytes, references and input position', () => {
  const drafts = new ComposerDrafts()
  const draft = drafts.forThread('work', 'chat', 'first')
  draft.update(value => ({
    ...value, text: 'Please read @plan.md',
    pending: [{ name: 'notes.txt', payload: { name: 'notes.txt', mime: 'text/plain', data: 'aGVsbG8=' } }],
    refs: [{ path: '/workspace/plan.md', name: 'plan.md', dir: '/workspace', kind: 'file', label: '@plan.md' }],
    selectionStart: 7, selectionEnd: 11, selectionDirection: 'backward', scrollTop: 40,
  }))
  drafts.forThread('work', 'chat', 'second').update(value => ({ ...value, text: 'Other draft' }))
  const restored = get(drafts.forThread('work', 'chat', 'first'))
  assert.equal(restored.text, 'Please read @plan.md')
  assert.equal(restored.pending[0].payload.data, 'aGVsbG8=')
  assert.match(composeMessage(restored.text, restored.refs), /Referenced files:[\s\S]*\/workspace\/plan\.md/)
  assert.deepEqual([restored.selectionStart, restored.selectionEnd, restored.selectionDirection, restored.scrollTop], [7, 11, 'backward', 40])
})

test('drafts stay separate across profiles and thread kinds with identical ids', () => {
  const drafts = new ComposerDrafts()
  for (const [profile, kind] of [['work', 'chat'], ['personal', 'chat'], ['work', 'run']] as const) {
    drafts.forThread(profile, kind, 'same').update(value => ({ ...value, text: `${profile}/${kind}` }))
  }
  assert.equal(get(drafts.forThread('work', 'chat', 'same')).text, 'work/chat')
  assert.equal(get(drafts.forThread('personal', 'chat', 'same')).text, 'personal/chat')
  assert.equal(get(drafts.forThread('work', 'run', 'same')).text, 'work/run')
})

test('profile and chat ids containing separators do not collide', () => {
  const drafts = new ComposerDrafts()
  drafts.forThread('work:chat', 'chat', 'id').update(value => ({ ...value, text: 'First' }))
  drafts.forThread('work', 'chat', 'chat:id').update(value => ({ ...value, text: 'Second' }))
  assert.equal(get(drafts.forThread('work:chat', 'chat', 'id')).text, 'First')
  assert.equal(get(drafts.forThread('work', 'chat', 'chat:id')).text, 'Second')
})
