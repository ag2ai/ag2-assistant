// Parser tests for the MCP smart-paste box. Run: node --test src/lib
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseMcpPaste, guessName, sanitizeName, catalogServer, MCP_CATALOG } from './mcp.ts'

test('standard mcpServers snippet (the README format)', () => {
  const { servers, error } = parseMcpPaste(`{
    "mcpServers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x" }
      }
    }
  }`)
  assert.equal(error, '')
  assert.equal(servers.length, 1)
  assert.deepEqual(servers[0], {
    name: 'github',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-github'],
    env: { GITHUB_PERSONAL_ACCESS_TOKEN: 'ghp_x' },
    cwd: '',
  })
})

test('multiple servers in one snippet parse to multiple entries', () => {
  const { servers } = parseMcpPaste(`{"mcpServers": {
    "a": {"command": "npx", "args": ["-y", "pkg-a"]},
    "b": {"command": "uvx", "args": ["pkg-b"]}
  }}`)
  assert.deepEqual(servers.map((s) => s.name), ['a', 'b'])
})

test('bare single config object, name guessed from package', () => {
  const { servers } = parseMcpPaste('{"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}')
  assert.equal(servers[0].name, 'memory')
  assert.equal(servers[0].command, 'npx')
})

test('name→config map without the mcpServers wrapper', () => {
  const { servers } = parseMcpPaste('{"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}')
  assert.equal(servers[0].name, 'fetch')
})

test('fragment with unbalanced braces ("name": {...}) is repaired', () => {
  const { servers, error } = parseMcpPaste('"weather": { "command": "uvx", "args": ["mcp-weather"] }')
  assert.equal(error, '')
  assert.equal(servers[0].name, 'weather')
})

test('trailing commas are tolerated', () => {
  const { servers } = parseMcpPaste('{"mcpServers": {"t": {"command": "npx", "args": ["-y", "x",],},},}')
  assert.equal(servers[0].name, 't')
})

test('command line splits command/args and guesses name', () => {
  const { servers } = parseMcpPaste('npx -y @modelcontextprotocol/server-github')
  assert.deepEqual(servers[0], {
    name: 'github', command: 'npx', args: ['-y', '@modelcontextprotocol/server-github'], env: {}, cwd: '',
  })
})

test('command line with leading env vars and quoted args', () => {
  const { servers } = parseMcpPaste('API_KEY=abc npx -y some-mcp "/Users/me/My Docs"')
  assert.deepEqual(servers[0].env, { API_KEY: 'abc' })
  assert.deepEqual(servers[0].args, ['-y', 'some-mcp', '/Users/me/My Docs'])
})

test('uvx command line names from the python package', () => {
  const { servers } = parseMcpPaste('uvx mcp-server-fetch')
  assert.equal(servers[0].name, 'fetch')
})

test('version pins are stripped from guessed names', () => {
  assert.equal(guessName('npx', ['-y', '@scope/server-thing@1.2.3']), 'thing')
})

test('direct binary command names itself', () => {
  assert.equal(guessName('/usr/local/bin/my-weather-mcp', []), 'my-weather')
})

test('nonsense text errors, empty text does not', () => {
  assert.notEqual(parseMcpPaste('{this is not json').error, '')
  assert.equal(parseMcpPaste('').error, '')
  assert.equal(parseMcpPaste('').servers.length, 0)
})

test('sanitizeName matches the backend name rule', () => {
  assert.equal(sanitizeName('My Server!'), 'My-Server')
  assert.equal(sanitizeName('---'), '')
  assert.equal(sanitizeName('a'.repeat(80)).length, 64)
})

test('catalogServer folds inputs into args and env', () => {
  const files = MCP_CATALOG.find((e) => e.id === 'repo-files')
  assert.ok(files)
  const server = catalogServer(files, { folder: '/tmp/proj' })
  assert.equal(server.args.at(-1), '/tmp/proj')
  assert.ok(server.allowed_tools?.includes('read_file'))
  const gh = MCP_CATALOG.find((e) => e.id === 'github')
  assert.ok(gh)
  assert.deepEqual(catalogServer(gh, { GITHUB_PERSONAL_ACCESS_TOKEN: 't' }).env, { GITHUB_PERSONAL_ACCESS_TOKEN: 't' })
})
