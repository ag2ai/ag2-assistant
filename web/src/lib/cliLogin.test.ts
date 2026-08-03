import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ADAPTER_PKG,
  CLI_TYPE,
  agentAvailability,
  canUseCliLogin,
  cliDefaultLabel,
  cliNote,
} from './cliLogin.ts'

const LOCAL = (available: boolean) => ({
  mode: 'local',
  bridge: null,
  connected: true,
  agents: [{ name: 'claude', label: 'Claude Code', available }],
})
const BRIDGE = (connected: boolean, available = true) => ({
  mode: 'bridge',
  bridge: 'host:8811',
  connected,
  agents: connected ? [{ name: 'claude', label: 'Claude Code', available }] : [],
})
const CATALOG = { models: [{ id: 'opus', name: 'Opus' }], current: 'sonnet', reason: '' }

test('agentAvailability reads the local inventory', () => {
  assert.deepEqual(agentAvailability(LOCAL(true), 'claude'), {
    loaded: true, mode: 'local', connected: true, available: true,
  })
  assert.equal(agentAvailability(LOCAL(false), 'claude').available, false)
  assert.equal(agentAvailability(LOCAL(true), 'codex').available, false) // absent row
})

test('agentAvailability is unloaded until the read lands', () => {
  for (const missing of [null, undefined]) {
    const a = agentAvailability(missing, 'claude')
    assert.equal(a.loaded, false)
    assert.equal(a.available, false)
    assert.equal(a.mode, 'local') // safe default, and nothing is claimed while unloaded
  }
})

test('agentAvailability treats a disconnected bridge as unavailable', () => {
  assert.equal(agentAvailability(BRIDGE(true), 'claude').available, true)
  assert.equal(agentAvailability(BRIDGE(false), 'claude').available, false)
  assert.equal(agentAvailability(BRIDGE(true), 'claude').mode, 'bridge')
})

test('the local gate needs a catalog that actually came back', () => {
  const avail = agentAvailability(LOCAL(true), 'claude')
  assert.equal(canUseCliLogin(avail, CATALOG), true)
  assert.equal(canUseCliLogin(avail, undefined), false) // not asked yet
  assert.equal(canUseCliLogin(avail, 'loading'), false) // probe in flight
  assert.equal(canUseCliLogin(avail, { models: [], current: '', reason: 'probe_failed' }), false)
  assert.equal(canUseCliLogin(avail, { models: [], current: '', reason: '' }), true) // answered, no models
})

test('the bridge gate rides the inventory — no catalog exists there by design', () => {
  assert.equal(canUseCliLogin(agentAvailability(BRIDGE(true), 'claude'), undefined), true)
  assert.equal(canUseCliLogin(agentAvailability(BRIDGE(false), 'claude'), undefined), false)
})

test('an unavailable adapter never opens the gate', () => {
  assert.equal(canUseCliLogin(agentAvailability(LOCAL(false), 'claude'), CATALOG), false)
  assert.equal(canUseCliLogin(null, CATALOG), false)
})

test('cliNote names the npm package when the adapter is missing', () => {
  const note = cliNote('claude', agentAvailability(LOCAL(false), 'claude'), undefined)
  assert.match(note, /npm i -g @agentclientprotocol\/claude-agent-acp/)
  assert.match(cliNote('codex', agentAvailability(LOCAL(false), 'codex'), undefined), /codex-acp/)
})

test('cliNote stays quiet while the reads are in flight', () => {
  assert.equal(cliNote('claude', agentAvailability(null, 'claude'), undefined), '')
  assert.equal(cliNote('claude', agentAvailability(LOCAL(true), 'claude'), 'loading'), '')
  assert.equal(cliNote('claude', agentAvailability(LOCAL(true), 'claude'), CATALOG), '')
})

test('cliNote explains the bridge cases', () => {
  assert.match(
    cliNote('claude', agentAvailability(BRIDGE(false), 'claude'), undefined),
    /AG2ASSISTANT_ACP_BRIDGE/
  )
  assert.match(
    cliNote('claude', agentAvailability(BRIDGE(true), 'claude'), undefined),
    /CLI's own model/
  )
})

test('cliNote tells an installed-but-silent adapter apart from a missing one', () => {
  const avail = agentAvailability(LOCAL(true), 'claude')
  assert.match(cliNote('claude', avail, { models: [], reason: 'probe_failed' }), /logged in/)
  assert.match(
    cliNote('claude', avail, { models: [], reason: 'adapter_missing' }),
    /npm i -g @agentclientprotocol\/claude-agent-acp/
  )
})

test('cliDefaultLabel names the CLI selection when the adapter reported one', () => {
  assert.equal(cliDefaultLabel(CATALOG), 'CLI default (sonnet)')
  assert.equal(cliDefaultLabel({ models: [], current: '', reason: '' }), 'CLI default')
  assert.equal(cliDefaultLabel('loading'), 'CLI default')
  assert.equal(cliDefaultLabel(undefined), 'CLI default')
})

test('the type map matches the backend CLI-login types', () => {
  assert.deepEqual(CLI_TYPE, { claude: 'claude_code', codex: 'codex' })
  assert.deepEqual(Object.keys(ADAPTER_PKG), ['claude', 'codex'])
})
