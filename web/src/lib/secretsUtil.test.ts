import { test } from 'node:test'
import assert from 'node:assert/strict'
import { autoSecretName, sortForProvider } from './secretsUtil.ts'

test('autoSecretName derives from config name + last4', () => {
  assert.equal(autoSecretName('My GPT', 'sk-abc-1234'), 'My GPT key …1234')
  assert.equal(autoSecretName('', 'sk-abc-1234'), 'API key …1234')
  assert.equal(autoSecretName('  X  ', ' sk-9 '), 'X key …sk-9')
})

test('sortForProvider soft-sorts tag matches first, keeps everything', () => {
  const s = [
    { id: '1', provider: '' },
    { id: '2', provider: 'openai' },
    { id: '3', provider: 'gemini' },
    { id: '4', provider: 'openai' },
  ]
  assert.deepEqual(sortForProvider(s, 'openai').map((x) => x.id), ['2', '4', '1', '3'])
  assert.deepEqual(sortForProvider(s, '').map((x) => x.id), ['1', '2', '3', '4'])
})
