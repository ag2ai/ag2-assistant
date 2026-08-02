import { test } from 'node:test'
import assert from 'node:assert/strict'
import { McpHealth, McpServer, ProfileHealth, ProfileSettings } from './settings.ts'

const settings = {
  keys: {
    openai: { set: true, hint: '…1234' },
    // secrets.status() reports Ollama's base URL instead of a key hint.
    ollama: { set: true, base_url: 'http://localhost:11434' },
  },
  voice_available: { gemini: false, openai: true },
  assistant: { provider: 'openai', model: 'gpt-5' },
  llm_override: null,
  llm_active: 'c1',
  live_override: null,
  live_active: null,
  codex: { signed_in: false, source: null, account_id: null, expires_at: null },
  voice_provider: 'openai',
  mcp_servers: [],
  focuses: [],
  reply_timeout_s: 120.0,
  fs: { home: '/home/u', cwd: '/home/u/app', workspace: '/home/u/ws' },
}

test('ProfileSettings accepts the heterogeneous provider key map', () => {
  const parsed = ProfileSettings.parse(settings)
  assert.equal(parsed.keys.openai.hint, '…1234')
  assert.equal(parsed.keys.ollama.base_url, 'http://localhost:11434')
})

test('ProfileSettings allows a null effective active id', () => {
  assert.equal(ProfileSettings.parse(settings).live_active, null)
})

test('McpServer exposes env keys, never env values', () => {
  const parsed = McpServer.parse({
    name: 'fs', enabled: true, command: 'npx', args: ['-y', 'mcp-fs'], cwd: null,
    allowed_tools: [], blocked_tools: [], env_keys: ['TOKEN'], env: { TOKEN: 'secret' },
  })
  assert.deepEqual(parsed.env_keys, ['TOKEN'])
  assert.equal('env' in parsed, false)
})

test('McpHealth discriminates a reachable server from an unreachable one', () => {
  assert.deepEqual(McpHealth.parse({ ok: true, tools: ['read'] }), { ok: true, tools: ['read'] })
  assert.deepEqual(McpHealth.parse({ ok: false, error: 'disabled' }), { ok: false, error: 'disabled' })
})

test('ProfileHealth carries the mcp servers and channel items on their rows', () => {
  const parsed = ProfileHealth.parse({
    overall: 'warn',
    checks: [
      { id: 'agent', label: 'Agent', state: 'ok', detail: '' },
      {
        id: 'mcp', label: 'MCP', state: 'warn', detail: '1 server',
        servers: [{ name: 'fs', enabled: true }],
      },
      {
        id: 'channels', label: 'Channels', state: 'off', detail: '',
        items: [{ platform: 'telegram', active: false, error: null, token_present: false }],
      },
    ],
  })
  assert.equal(parsed.checks[1].servers?.[0].name, 'fs')
  assert.equal(parsed.checks[2].items?.[0].error, null)
})
