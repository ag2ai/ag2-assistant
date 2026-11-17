import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  newOllamaOnboardingDraft,
  ollamaOnboardingConfig,
  ollamaOnboardingReady,
} from './onboardingModels.ts'

test('local Ollama starts with the local host and a model', () => {
  const draft = newOllamaOnboardingDraft('local')
  assert.equal(draft.host, 'http://localhost:11434')
  assert.equal(draft.model, 'llama3.2')
  assert.equal(ollamaOnboardingReady('local', draft), true)
  assert.deepEqual(ollamaOnboardingConfig('local', draft), {
    name: 'Ollama · Local',
    type: 'ollama',
    model: 'llama3.2',
    host: 'http://localhost:11434',
  })
})

test('remote Ollama requires and persists a custom host and model', () => {
  const draft = newOllamaOnboardingDraft('remote')
  assert.equal(ollamaOnboardingReady('remote', draft), false)
  assert.equal(ollamaOnboardingConfig('remote', draft), null)
  draft.host = 'https://ollama.example.test:11434'
  draft.model = 'qwen3:8b'
  assert.equal(ollamaOnboardingReady('remote', draft), true)
  assert.deepEqual(ollamaOnboardingConfig('remote', draft), {
    name: 'Ollama · Remote',
    type: 'ollama',
    model: 'qwen3:8b',
    host: 'https://ollama.example.test:11434',
  })
})

test('Ollama Cloud requires a key and only references a saved Secret', () => {
  const draft = newOllamaOnboardingDraft('cloud')
  draft.model = 'qwen3:cloud'
  assert.equal(ollamaOnboardingReady('cloud', draft), false)
  assert.equal(ollamaOnboardingConfig('cloud', draft), null)
  draft.apiKey = 'replacement-key'
  assert.equal(ollamaOnboardingReady('cloud', draft), true)
  assert.deepEqual(ollamaOnboardingConfig('cloud', draft), {
    name: 'Ollama Cloud',
    type: 'ollama_cloud',
    model: 'qwen3:cloud',
  })
  assert.deepEqual(ollamaOnboardingConfig('cloud', draft, 'secret-1'), {
    name: 'Ollama Cloud',
    type: 'ollama_cloud',
    model: 'qwen3:cloud',
    secret_id: 'secret-1',
  })
})

test('all Ollama modes require a model name', () => {
  for (const mode of ['local', 'remote', 'cloud'] as const) {
    const draft = newOllamaOnboardingDraft(mode)
    draft.model = '  '
    draft.host = 'http://localhost:11434'
    draft.apiKey = 'key'
    assert.equal(ollamaOnboardingReady(mode, draft), false)
    assert.equal(ollamaOnboardingConfig(mode, draft), null)
  }
})
