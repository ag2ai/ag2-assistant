<script lang="ts">
  // Settings → "MCP servers" section. Three ways in, easiest first:
  //   1. Quick add — curated catalog entries, one click (plus env/folder inputs
  //      where a server needs them).
  //   2. Smart paste — a single box that accepts the README-style
  //      {"mcpServers": {...}} JSON or a plain command line; parsed entries are
  //      previewed (name/env editable) before anything is saved.
  //   3. Manual form — the original field-by-field form, behind "Add manually".
  // Every add auto-runs the health check so "did it work?" is answered inline.
  // Self-contained like Channels.svelte: owns its list state; the add/delete
  // endpoints return the updated mcp_servers list so no full settings reload.
  import { onMount } from 'svelte'
  import { api } from '../transport/api/index.ts'
  import { parseMcpPaste, MCP_CATALOG, catalogServer, type CatalogEntry, type McpServerDraft } from '../lib/mcp.ts'
  import { errText } from '../lib/errors.ts'
  import type { McpServerRequest } from '../transport/api/settings.ts'
  import type { McpServer } from '../schemas/index.ts'
  import Icon from './Icon.svelte'

  // One open shape rather than a union of the three probe states: the markup reads
  // health[name], and element access with a computed key never narrows a union.
  type McpProbe = { checking?: boolean; ok?: boolean; tools?: string[]; error?: string }

  let servers: McpServer[] = $state([])
  let health: Record<string, McpProbe | undefined> = $state({})
  let busy = $state(false)
  let err = $state('')

  // Smart paste
  let paste = $state('')
  let pasteEl: HTMLTextAreaElement | undefined = $state()
  let drafts: McpServerDraft[] = $state([])   // parsed, user-editable entries awaiting confirm
  let parseErr = $state('')

  // Quick-add catalog: entry id currently expanded for inputs + its values
  let openEntry = $state('')
  let entryValues: Record<string, string> = $state({})

  // Manual form (the old path, now the fallback) — raw text the server splits.
  let showManual = $state(false)
  let manual = $state({ name: '', command: '', args: '', cwd: '', allowed_tools: '', blocked_tools: '', env: '' })

  const names = $derived(new Set(servers.map((s) => s.name)))

  onMount(async () => {
    try {
      const s = await api.settings()
      servers = s.mcp_servers
    } catch (e) { err = errText(e) }
  })

  async function check(name: string) {
    health = { ...health, [name]: { checking: true } }
    try {
      health = { ...health, [name]: await api.healthMcpServer(name) }
    } catch (e) {
      health = { ...health, [name]: { ok: false, error: errText(e) } }
    }
  }

  // Add one server payload, refresh the list from the response, health-check it.
  async function add(server: McpServerRequest) {
    err = ''; busy = true
    try {
      const res = await api.addMcpServer(server)
      servers = res.mcp_servers
      busy = false
      await check(server.name)
      return true
    } catch (e) {
      err = errText(e)
      busy = false
      return false
    }
  }

  async function remove(name: string) {
    err = ''; busy = true
    try {
      const res = await api.deleteMcpServer(name)
      servers = res.mcp_servers
      const { [name]: _gone, ...rest } = health
      health = rest
    } catch (e) { err = errText(e) }
    busy = false
  }

  // --- smart paste ---
  function onPaste(el: HTMLTextAreaElement) {
    paste = el.value
    // Grow the box to fit the pasted config (CSS caps it, then it scrolls) —
    // the native resize handle is disabled since drag-resize paints over the
    // modal's flex column instead of reflowing it.
    el.style.height = 'auto'
    el.style.height = el.value ? el.scrollHeight + 2 + 'px' : ''
    const { servers: parsed, error } = parseMcpPaste(el.value)
    parseErr = error
    drafts = parsed
  }
  async function addDrafts() {
    for (const d of drafts) {
      if (!(await add(d))) return   // stop on first failure; err is shown
    }
    paste = ''; drafts = []; parseErr = ''
    if (pasteEl) pasteEl.style.height = ''  // back to the CSS min-height
  }

  // --- quick-add catalog ---
  function clickEntry(entry: CatalogEntry) {
    if (names.has(entry.id) || busy) return
    if (!entry.inputs.length) { addEntry(entry); return }
    openEntry = openEntry === entry.id ? '' : entry.id
    entryValues = {}
  }
  const entryReady = (entry: CatalogEntry) =>
    entry.inputs.every((i) => !i.required || (entryValues[i.key] || '').trim())
  async function addEntry(entry: CatalogEntry) {
    if (await add(catalogServer(entry, entryValues))) { openEntry = ''; entryValues = {} }
  }

  // --- manual form ---
  async function addManual() {
    if (await add(manual)) {
      manual = { name: '', command: '', args: '', cwd: '', allowed_tools: '', blocked_tools: '', env: '' }
      showManual = false
    }
  }

  const cmdline = (s: { command: string; args: string[] }) => [s.command, ...s.args].join(' ')
</script>

{#if err}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

{#if !servers.length}
  <p class="muted" style="font-size:13px">No MCP servers configured — add one below to give the assistant new tools.</p>
{/if}
{#each servers as server (server.name)}
  {@const h = health[server.name]}
  <div class="mcprow">
    <div class="mcpmeta">
      <strong>{server.name}</strong>
      <span>{cmdline(server)}</span>
      {#if server.env_keys.length}<span>env: {server.env_keys.join(', ')}</span>{/if}
      {#if h}
        {#if h.checking}
          <span>checking…</span>
        {:else}
          <span class:mcpbad={!h.ok}>
            {h.ok ? `healthy · ${(h.tools || []).length} tools` : h.error}
          </span>
        {/if}
      {/if}
    </div>
    <button class="open" disabled={busy} onclick={() => check(server.name)}>Check</button>
    <button class="linkbtn danger" disabled={busy} onclick={() => remove(server.name)}>Delete</button>
  </div>
{/each}

<!-- 1. quick add -->
<div class="mcpcat">
  {#each MCP_CATALOG as entry (entry.id)}
    {@const added = names.has(entry.id)}
    <button
      class="mcpcatcard" class:added class:openinputs={openEntry === entry.id}
      disabled={busy || added}
      onclick={() => clickEntry(entry)}
      title={added ? 'Already added' : `Runs: ${[entry.command, ...entry.args].join(' ')} — needs ${entry.requires}`}
    >
      <span class="mcpcathead">
        {#if added}<Icon name="check" size={13} />{:else}<Icon name="plus" size={13} />{/if}
        {entry.label}
      </span>
      <span class="mcpcatblurb">{entry.blurb}</span>
    </button>
  {/each}
</div>
{#each MCP_CATALOG.filter((e) => e.id === openEntry) as entry (entry.id)}
  <div class="mcpinputs">
    {#each entry.inputs as input (input.key)}
      <div class="keyrow">
        <span class="kp">{input.label}</span>
        <input
          type={input.kind === 'env' ? 'password' : 'text'}
          placeholder={input.ph}
          bind:value={entryValues[input.key]}
        />
      </div>
    {/each}
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (openEntry = '')}>Cancel</button>
      <button class="open" disabled={busy || !entryReady(entry)} onclick={() => addEntry(entry)}>Add {entry.label}</button>
    </div>
  </div>
{/each}

<!-- 2. smart paste -->
<textarea
  bind:this={pasteEl}
  class="mcppaste"
  placeholder={'Or paste an MCP config — the {"mcpServers": …} JSON from a server\'s README, or a command line like: npx -y @modelcontextprotocol/server-github'}
  value={paste}
  oninput={(e) => onPaste(e.currentTarget)}
></textarea>
{#if parseErr}
  <p class="muted" style="font-size:12px;color:var(--danger);margin:0">{parseErr}</p>
{/if}
{#if drafts.length}
  {#each drafts as d, i (i)}
    <div class="mcpdraft">
      <div class="keyrow">
        <span class="kp">Name</span>
        <input type="text" bind:value={d.name} />
        <span class="mcpcmd" title={cmdline(d)}>{cmdline(d)}</span>
      </div>
      {#each Object.keys(d.env) as key (key)}
        <div class="keyrow">
          <span class="kp mcpenvkey" title={key}>{key}</span>
          <input type="password" bind:value={d.env[key]} />
        </div>
      {/each}
    </div>
  {/each}
  <div class="keyrow" style="justify-content:flex-end">
    <button class="open" disabled={busy || drafts.some((d) => !d.name)} onclick={addDrafts}>
      Add {drafts.length === 1 ? drafts[0].name : `${drafts.length} servers`}
    </button>
  </div>
{/if}

<!-- 3. manual form -->
{#if !showManual}
  <button class="open" style="justify-self:start" onclick={() => (showManual = true)}>Add manually</button>
{:else}
  <div class="mcpform">
    <input placeholder="name, e.g. github" bind:value={manual.name} />
    <input placeholder="command, e.g. npx" bind:value={manual.command} />
    <input placeholder="args, e.g. -y @modelcontextprotocol/server-github" bind:value={manual.args} />
    <input placeholder="cwd (optional)" bind:value={manual.cwd} />
    <input placeholder="allowed tools, comma-separated (optional)" bind:value={manual.allowed_tools} />
    <input placeholder="blocked tools, comma-separated (optional)" bind:value={manual.blocked_tools} />
    <textarea placeholder="env, one KEY=VALUE per line (optional)" bind:value={manual.env}></textarea>
    <div class="keyrow" style="grid-column:1/-1">
      <button class="open" disabled={busy || !manual.name || !manual.command} onclick={addManual}>Add MCP server</button>
      <button class="linkbtn" onclick={() => (showManual = false)}>Cancel</button>
    </div>
  </div>
{/if}
