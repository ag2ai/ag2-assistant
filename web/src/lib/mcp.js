// MCP quick-add: paste parsing + curated catalog.
//
// Most MCP servers publish a Claude-Desktop-style JSON snippet in their README:
//   { "mcpServers": { "github": { "command": "npx", "args": [...], "env": {...} } } }
// parseMcpPaste() turns that snippet — or the common variants of it — into the
// {name, command, args, env, cwd} shape POST /settings/mcp expects, so adding a
// server is paste → preview → confirm instead of decomposing it into form fields.

// Mirrors the backend name rule (_MCP_NAME_RE in assistant/settings.py).
const NAME_OK = /^[A-Za-z0-9_.-]{1,64}$/

// Launcher/interpreter commands that never make a good server name.
const LAUNCHERS = new Set(['npx', 'uvx', 'uv', 'npm', 'pnpm', 'bunx', 'node', 'python', 'python3', 'docker', 'deno'])

export function sanitizeName(raw) {
  const name = String(raw || '').trim().replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)
  return NAME_OK.test(name) ? name : ''
}

// "mcp-server-fetch" → "fetch", "my-weather-mcp" → "my-weather".
const stripMcpWords = (base) =>
  base.replace(/^(mcp[-_])?(server[-_])?/, '').replace(/[-_]?(mcp|server)$/, '')

// Best-effort server name from the command line: the package being launched
// ("@modelcontextprotocol/server-github" → "github", "mcp-server-fetch" →
// "fetch"), else the command itself when it's not a generic launcher.
export function guessName(command, args = []) {
  for (const arg of args) {
    const a = String(arg)
    if (a.startsWith('-') || a.includes('=')) continue
    if (/[\\/]|^\.|^~/.test(a) && !a.startsWith('@')) continue // paths aren't packages
    const base = a.split('/').pop().replace(/@[\w.^~-]+$/, '') // strip version pin
    const name = sanitizeName(stripMcpWords(base) || base)
    if (name) return name
  }
  const cmd = String(command || '').split('/').pop()
  if (cmd && !LAUNCHERS.has(cmd)) return sanitizeName(stripMcpWords(cmd) || cmd)
  return 'mcp'
}

// One server config object ({command, args?, env?, cwd?}) → normalised entry.
function fromConfig(name, cfg) {
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) return null
  const command = String(cfg.command || '').trim()
  if (!command) return null
  const args = Array.isArray(cfg.args) ? cfg.args.map(String) : []
  const env = {}
  if (cfg.env && typeof cfg.env === 'object' && !Array.isArray(cfg.env)) {
    for (const [k, v] of Object.entries(cfg.env)) if (String(k).trim()) env[String(k)] = String(v)
  }
  return {
    name: sanitizeName(name) || guessName(command, args),
    command,
    args,
    env,
    cwd: typeof cfg.cwd === 'string' ? cfg.cwd.trim() : '',
  }
}

// Shell-ish tokenizer for a pasted command line (respects single/double quotes).
function tokenize(line) {
  const tokens = []
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g
  let m
  while ((m = re.exec(line))) tokens.push(m[1] ?? m[2] ?? m[3])
  return tokens
}

// "GITHUB_TOKEN=x npx -y pkg" → env from leading KEY=VALUE tokens, then command+args.
function fromCommandLine(line) {
  const tokens = tokenize(line.replace(/^\$\s+/, ''))
  const env = {}
  while (tokens.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[0])) {
    const [k, ...rest] = tokens.shift().split('=')
    env[k] = rest.join('=')
  }
  if (!tokens.length) return null
  const [command, ...args] = tokens
  return { name: guessName(command, args), command, args, env, cwd: '' }
}

// Whole parsed JSON document → list of server entries. Accepts the standard
// {"mcpServers": {...}} wrapper, a bare name→config map, or a single config.
function fromJson(doc) {
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return []
  const map = doc.mcpServers || doc.mcp_servers || doc.servers
  if (map && typeof map === 'object' && !Array.isArray(map)) {
    return Object.entries(map).map(([name, cfg]) => fromConfig(name, cfg)).filter(Boolean)
  }
  if (doc.command) return [fromConfig(doc.name, doc)].filter(Boolean)
  // Bare name→config map pasted without the mcpServers wrapper.
  const entries = Object.entries(doc)
  if (entries.length && entries.every(([, v]) => v && typeof v === 'object' && v.command)) {
    return entries.map(([name, cfg]) => fromConfig(name, cfg)).filter(Boolean)
  }
  return []
}

/** Parse pasted text into MCP server entries.
 *  Returns { servers: [{name, command, args, env, cwd}], error: '' }. `error`
 *  is set (and servers empty) only when the text is non-empty but unusable. */
export function parseMcpPaste(text) {
  const raw = String(text || '').trim()
  if (!raw) return { servers: [], error: '' }

  if (raw.startsWith('{') || raw.startsWith('"')) {
    // README snippets are often fragments: `"github": { ... }` without the outer
    // braces, or with trailing commas. Try as-is, then lightly repaired.
    const candidates = [raw, `{${raw}}`].map((t) => t.replace(/,\s*([}\]])/g, '$1'))
    for (const candidate of candidates) {
      try {
        const servers = fromJson(JSON.parse(candidate))
        if (servers.length) return { servers, error: '' }
      } catch { /* try next candidate */ }
    }
    return { servers: [], error: 'Could not read that as MCP JSON — expected {"mcpServers": {...}} or a single {"command": ...} config.' }
  }

  const single = fromCommandLine(raw.split('\n')[0])
  if (single) return { servers: [single], error: '' }
  return { servers: [], error: 'Could not read that — paste a JSON config or a command line.' }
}

// Read-only tool subset for the filesystem server — mirrors _REPO_FILES_READ_TOOLS
// in gateway/app.py (the project-folder flow seeds the same entry).
export const FILES_READ_TOOLS = [
  'read_file',
  'read_multiple_files',
  'list_directory',
  'directory_tree',
  'search_files',
  'get_file_info',
  'list_allowed_directories',
]

// Curated quick-add entries. `inputs` are collected before adding:
//   {key, label, ph, kind: 'env'|'arg', required} — env values go to server.env,
//   arg values are appended to args. Blurbs state only what the server does.
export const MCP_CATALOG = [
  {
    id: 'repo-files',
    label: 'Project files',
    blurb: 'read-only access to a folder — browse and search, never write',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem'],
    allowed_tools: FILES_READ_TOOLS,
    inputs: [{ key: 'folder', label: 'Folder', ph: '/path/to/project', kind: 'arg', required: true }],
    requires: 'Node.js',
  },
  {
    id: 'browser',
    label: 'Browser',
    blurb: 'drive a real web browser — navigate, click, type, screenshot',
    command: 'npx',
    // --sandbox re-enables Chromium's sandbox (Playwright defaults it OFF), wanted
    // here since an agent drives this browser to arbitrary pages. NOTE: the flag is
    // currently inert upstream (microsoft/playwright-mcp#883 — CLI sandbox flags
    // aren't forwarded to launch); it self-heals when that's fixed. Working today:
    // --config pointing at {"browser":{"launchOptions":{"chromiumSandbox":true}}}.
    args: ['-y', '@playwright/mcp@latest', '--sandbox'],
    inputs: [],
    requires: 'Node.js + Chrome',
  },
  {
    id: 'fetch',
    label: 'Fetch',
    blurb: 'fetch a web page by URL and convert it to markdown',
    command: 'uvx',
    args: ['mcp-server-fetch'],
    inputs: [],
    requires: 'Python (uv)',
  },
  {
    id: 'time',
    label: 'Time',
    blurb: 'current time and timezone conversions',
    command: 'uvx',
    args: ['mcp-server-time'],
    inputs: [],
    requires: 'Python (uv)',
  },
  {
    id: 'github',
    label: 'GitHub',
    blurb: 'repos, issues and pull requests via the GitHub API',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-github'],
    inputs: [{ key: 'GITHUB_PERSONAL_ACCESS_TOKEN', label: 'Access token', ph: 'ghp_…', kind: 'env', required: true }],
    requires: 'Node.js',
  },
]

/** Catalog entry + collected input values → server payload for POST /settings/mcp. */
export function catalogServer(entry, values = {}) {
  const args = [...entry.args]
  const env = {}
  for (const input of entry.inputs || []) {
    const value = String(values[input.key] || '').trim()
    if (!value) continue
    if (input.kind === 'arg') args.push(value)
    else env[input.key] = value
  }
  const server = { name: entry.id, command: entry.command, args, env, cwd: '' }
  if (entry.allowed_tools) server.allowed_tools = [...entry.allowed_tools]
  return server
}
