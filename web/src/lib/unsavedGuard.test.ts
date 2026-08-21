import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { setUnsavedGuard, confirmDiscard, discardPrompt } from './unsavedGuard.ts'

afterEach(() => setUnsavedGuard(null))

test('confirmDiscard: no guard registered proceeds without prompting', () => {
  let asked = false
  const ok = confirmDiscard(() => { asked = true; return false })
  assert.equal(ok, true)
  assert.equal(asked, false)
})

test('confirmDiscard: a clean editor proceeds without prompting', () => {
  let asked = false
  setUnsavedGuard(() => false)
  const ok = confirmDiscard(() => { asked = true; return false })
  assert.equal(ok, true)
  assert.equal(asked, false)
})

test('confirmDiscard: a dirty editor prompts and proceeds when accepted', () => {
  setUnsavedGuard(() => true)
  const seen: (string | undefined)[] = []
  const ok = confirmDiscard((msg) => { seen.push(msg); return true })
  assert.equal(ok, true)
  assert.deepEqual(seen, [discardPrompt()])
})

test('confirmDiscard: a dirty editor blocks when the prompt is declined', () => {
  setUnsavedGuard(() => true)
  const ok = confirmDiscard(() => false)
  assert.equal(ok, false)
})

test('setUnsavedGuard: a non-function argument clears the guard', () => {
  setUnsavedGuard(() => true)
  setUnsavedGuard(null)
  assert.equal(confirmDiscard(() => false), true)
})
