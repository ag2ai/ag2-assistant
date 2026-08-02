import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Secret, SecretConflict, SecretList, SecretSaved } from './secret.ts'

test('Secret accepts the safe view the gateway returns', () => {
  const row = { id: 's1', name: 'OpenAI', provider: 'openai', default: true, hint: '…abcd' }
  assert.deepEqual(Secret.parse(row), { ...row, used_by: [] })
})

test('SecretList carries used_by names per secret', () => {
  const payload = {
    secrets: [
      { id: 's1', name: 'OpenAI', provider: 'openai', default: false, hint: '', used_by: ['gpt'] },
    ],
  }
  assert.equal(SecretList.parse(payload).secrets[0].used_by[0], 'gpt')
})

test('Secret rejects a payload that leaks the raw value field name', () => {
  const parsed = Secret.parse({
    id: 's1', name: 'n', provider: '', default: false, hint: '', value: 'sk-leak',
  })
  assert.equal('value' in parsed, false)
})

test('SecretSaved wraps the view create and update echo back', () => {
  const parsed = SecretSaved.parse({
    ok: true,
    secret: { id: 's1', name: 'n', provider: 'openai', default: false, hint: '…9999' },
  })
  assert.equal(parsed.secret.id, 's1')
})

test('SecretConflict carries the secret that already holds the value', () => {
  const parsed = SecretConflict.parse({
    ok: false,
    error: 'that value is already stored',
    existing: { id: 's0', name: 'old', provider: 'openai', default: true, hint: '…9999' },
  })
  assert.equal(parsed.existing.name, 'old')
})
