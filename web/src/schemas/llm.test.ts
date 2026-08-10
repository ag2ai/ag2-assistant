import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LiveConfigList, LlmConfig, LlmConfigList, PingResult } from './llm.ts'

const base = {
  id: 'c1', name: 'GPT', type: 'openai', model: 'gpt-5', base_url: '', host: '',
  options: {}, builtin_tools: {}, secret_id: 's1', secret: { id: 's1', name: 'k', hint: '…1234' },
  secret_missing: false, key_source: 'secret', images: true,
  deps: { ok: true, extra: '', install: '' },
  shared_key: { env: 'OPENAI_API_KEY', set: true, hint: '…9999' },
  active: true,
}

test('LlmConfig accepts a saved config view', () => {
  assert.equal(LlmConfig.parse(base).key_source, 'secret')
})

test('LlmConfig keeps signed_in only for the subscription type', () => {
  const sub = LlmConfig.parse({ ...base, type: 'openai_subscription', signed_in: false })
  assert.equal(sub.signed_in, false)
  assert.equal(LlmConfig.parse(base).signed_in, undefined)
})

test('LlmConfig accepts a dangling secret reference', () => {
  const parsed = LlmConfig.parse({ ...base, secret: null, secret_missing: true })
  assert.equal(parsed.secret, null)
})

test('LlmConfig accepts the cli_login key source of the ACP types', () => {
  const cli = LlmConfig.parse({ ...base, type: 'codex', model: '', key_source: 'cli_login' })
  assert.equal(cli.key_source, 'cli_login')
})

test('LlmConfig rejects an unknown key_source', () => {
  assert.throws(() => LlmConfig.parse({ ...base, key_source: 'magic' }))
})

test('LlmConfigList allows a null active id and a null env override', () => {
  const parsed = LlmConfigList.parse({
    configs: [base], active: null, env_override: null, provider_deps: {},
    builtin_tools_by_type: {},
  })
  assert.equal(parsed.active, null)
})

test('LlmConfigList carries per-type provider deps for unconfigured types too', () => {
  const parsed = LlmConfigList.parse({
    configs: [], active: null, env_override: { provider: 'openai' },
    provider_deps: { anthropic: { ok: false, extra: 'anthropic', install: 'pip install x' } },
    builtin_tools_by_type: {},
  })
  assert.equal(parsed.provider_deps.anthropic.ok, false)
})

test('LiveConfigList carries the provider catalog', () => {
  const parsed = LiveConfigList.parse({
    configs: [], active: null,
    providers: [{ name: 'openai', default_model: 'realtime', default_voice: 'alloy' }],
  })
  assert.equal(parsed.providers[0].default_voice, 'alloy')
})

test('PingResult carries the measured latency', () => {
  assert.equal(PingResult.parse({ ok: true, reply: 'PONG', latency_ms: 42 }).latency_ms, 42)
})

test('LlmConfig carries the provider tools switched on', () => {
  const parsed = LlmConfig.parse({ ...base, builtin_tools: { web_search: {} } })
  assert.deepEqual(parsed.builtin_tools, { web_search: {} })
})

test('LlmConfigList carries per-type provider tool availability', () => {
  const parsed = LlmConfigList.parse({
    configs: [], active: null, env_override: null, provider_deps: {},
    builtin_tools_by_type: { gemini: ['web_search'], openai: [] },
  })
  assert.deepEqual(parsed.builtin_tools_by_type.gemini, ['web_search'])
  assert.deepEqual(parsed.builtin_tools_by_type.openai, [])
})
