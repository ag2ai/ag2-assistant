<script>
  import { onMount } from 'svelte'
  import { settingsOpen, voicePickerOpen, googleOpen, soundOnInput, memoryOpen, poweredByOpen, ag2View, onboardingOpen } from '../store.js'
  import { api } from '../transport/api.js'
  import { chime } from '../lib/chime.js'
  import Icon from './Icon.svelte'
  import Appearance from './Appearance.svelte'
  import FolderPicker from './FolderPicker.svelte'

  const PROVIDER_LABEL = { gemini: 'Gemini', openai: 'OpenAI', anthropic: 'Anthropic', ollama: 'Ollama' }
  // API-key rows. github is a stored token (skills registry), NOT a model provider,
  // so it lives here but never in the assistant/voice provider dropdowns.
  const KEY_ROWS = [
    { id: 'openai', label: 'OpenAI', ph: 'paste key' },
    { id: 'gemini', label: 'Gemini', ph: 'paste key' },
    { id: 'anthropic', label: 'Anthropic', ph: 'paste key' },
    { id: 'github', label: 'GitHub', ph: 'optional — raises skills-registry rate limit' },
  ]
  const VOICE_PROVIDERS = ['gemini', 'openai']

  let s = $state(null)            // GET /api/settings payload
  let google = $state(null)
  let drafts = $state({})         // provider -> input value
  let model = $state('')
  let mcp = $state({ name: '', command: '', args: '', cwd: '', allowed_tools: '', blocked_tools: '', env: '' })
  let mcpHealth = $state({})
  let err = $state('')
  let busy = $state(false)
  let editFolder = $state(false)   // project-folder picker expanded?

  function openFolderEdit() { editFolder = true }
  // one-click commit: the folder you're viewing in the picker applies immediately
  const commitFolder = (path) => run(() => api.setProjectFolder(path).then(() => { editFolder = false }))

  async function load() {
    try {
      s = await api.settings()
      model = s.assistant.model || ''
      drafts = { ollama: s.keys.ollama?.base_url || '' }
    } catch (e) { err = String(e.message || e) }
    try { google = await api.googleStatus() } catch {}
  }
  onMount(load)

  async function run(fn) {
    err = ''; busy = true
    try { await fn(); await load() } catch (e) { err = String(e.message || e) }
    busy = false
  }
  const saveKey = (p) => run(() => api.setKey(p, drafts[p] || '').then(() => { drafts[p] = '' }))
  const clearKey = (p) => run(() => api.setKey(p, ''))
  const saveOllama = () => run(() => api.setKey('ollama', drafts.ollama || ''))
  const saveLlm = (p) => run(() => api.setLlm(p, model))
  const saveVoiceProvider = (p) => run(() => api.setVoiceProvider(p))
  const saveMcp = () => run(() => api.addMcpServer(mcp).then(() => {
    mcp = { name: '', command: '', args: '', cwd: '', allowed_tools: '', blocked_tools: '', env: '' }
    mcpHealth = {}
  }))
  const deleteMcp = (name) => run(() => api.deleteMcpServer(name).then(() => {
    const { [name]: _deleted, ...rest } = mcpHealth
    mcpHealth = rest
  }))
  const healthMcp = (name) => run(async () => {
    mcpHealth = { ...mcpHealth, [name]: await api.healthMcpServer(name) }
  })

  const close = () => ($settingsOpen = false)
  const openVoice = () => { $settingsOpen = false; $voicePickerOpen = true }
  const openGoogle = () => { $settingsOpen = false; $googleOpen = true }
  const openMemory = () => { $settingsOpen = false; $memoryOpen = true }
  const openPoweredBy = () => { $settingsOpen = false; $poweredByOpen = true }
  const reRunSetup = () => { $settingsOpen = false; $onboardingOpen = true }
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal settings">
  <h2>Settings</h2>
  {#if err}<p class="muted" style="color:#d8552f">{err}</p>{/if}

  {#if !s}
    <p class="muted">Loading…</p>
  {:else}
    <div class="setscroll">
      <div class="setsec">Appearance</div>
      <Appearance />
      <button class="setrow" onclick={reRunSetup}>
        <span class="sk"><Icon name="sparkles" size={15} /> Re-run setup</span>
        <span class="sv">replay the first-run welcome & onboarding</span>
        <span class="sgo">Open →</span>
      </button>

      <div class="setsec">Project folder</div>
      {#if !editFolder}
        <button class="setrow" onclick={openFolderEdit}>
          <span class="sk"><Icon name="folder" size={15} /> {s.project_folder ? 'Folder' : 'Choose a folder'}</span>
          <span class="sv">{s.project_folder || 'the assistant can read this folder (read-only)'}</span>
          <span class="sgo">Change →</span>
        </button>
      {:else}
        <FolderPicker roots={s.fs || {}} start={s.project_folder || (s.fs && s.fs.cwd) || ''} {busy} onUse={commitFolder} />
        <div class="keyrow" style="justify-content:flex-end">
          <button class="linkbtn" onclick={() => (editFolder = false)}>Cancel</button>
        </div>
      {/if}

      <div class="setsec">API keys</div>
      {#each KEY_ROWS as k}
        <div class="keyrow">
          <span class="kp">{k.label}</span>
          <input type="password" placeholder={s.keys[k.id]?.set ? '•••• ' + s.keys[k.id].hint : k.ph} bind:value={drafts[k.id]} />
          <button class="open" disabled={busy} onclick={() => saveKey(k.id)}>Save</button>
          {#if s.keys[k.id]?.set}<button class="linkbtn" disabled={busy} onclick={() => clearKey(k.id)}>Clear</button>{/if}
        </div>
      {/each}
      <div class="keyrow">
        <span class="kp">Ollama</span>
        <input type="text" placeholder="http://localhost:11434" bind:value={drafts.ollama} />
        <button class="open" disabled={busy} onclick={saveOllama}>Save</button>
      </div>

      <div class="setsec">Assistant model</div>
      <div class="keyrow">
        <select bind:value={s.assistant.provider}>
          {#each Object.keys(PROVIDER_LABEL) as p}
            <option value={p} disabled={!s.available[p]}>{PROVIDER_LABEL[p]}{s.available[p] ? '' : ' (no key)'}</option>
          {/each}
        </select>
        <input type="text" placeholder="model, e.g. gemini-3.5-flash" bind:value={model} />
        <button class="open" disabled={busy} onclick={() => saveLlm(s.assistant.provider)}>Save</button>
      </div>

      <div class="setsec">Voice</div>
      {#if VOICE_PROVIDERS.some((p) => s.available[p])}
        <div class="keyrow">
          <select value={s.voice_provider} onchange={(e) => saveVoiceProvider(e.target.value)}>
            {#each VOICE_PROVIDERS as p}
              <option value={p} disabled={!s.available[p]}>{PROVIDER_LABEL[p]}{s.available[p] ? '' : ' (no key)'}</option>
            {/each}
          </select>
          <button class="open" onclick={openVoice}>Change voice →</button>
        </div>
      {:else}
        <p class="muted" style="font-size:13px">Add an OpenAI or Gemini key above to enable voice.</p>
      {/if}

      <div class="setsec">Memory</div>
      <button class="setrow" onclick={openMemory}>
        <span class="sk"><Icon name="brain" size={15} /> Memory</span>
        <span class="sv">what the assistant has learned about you</span>
        <span class="sgo">View & edit →</span>
      </button>

      <div class="setsec">AG2</div>
      <button class="setrow" onclick={openPoweredBy}>
        <span class="sk"><Icon name="zap" size={15} /> Powered by AG2</span>
        <span class="sv">the AG2 Beta primitives behind this app</span>
        <span class="sgo">View →</span>
      </button>
      <label class="setcheck">
        <input type="checkbox" bind:checked={$ag2View} />
        AG2 view — reveal live AG2 events + per-item provenance
      </label>

      <div class="setsec">Notifications</div>
      <label class="setcheck">
        <input type="checkbox" bind:checked={$soundOnInput} onchange={(e) => e.target.checked && chime()} />
        Play a sound when the assistant needs my input
      </label>

      <div class="setsec">MCP servers</div>
      {#if !(s.mcp_servers || []).length}
        <p class="muted" style="font-size:13px">No MCP servers configured.</p>
      {/if}
      {#each s.mcp_servers || [] as server}
        <div class="mcprow">
          <div class="mcpmeta">
            <strong>{server.name}</strong>
            <span>{server.command} {(server.args || []).join(' ')}</span>
            {#if server.env_keys?.length}<span>env: {server.env_keys.join(', ')}</span>{/if}
            {#if mcpHealth[server.name]}
              <span class:mcpbad={!mcpHealth[server.name].ok}>
                {mcpHealth[server.name].ok ? `healthy · ${(mcpHealth[server.name].tools || []).length} tools` : mcpHealth[server.name].error}
              </span>
            {/if}
          </div>
          <button class="open" disabled={busy} onclick={() => healthMcp(server.name)}>Check</button>
          <button class="linkbtn" disabled={busy} onclick={() => deleteMcp(server.name)}>Delete</button>
        </div>
      {/each}
      <div class="mcpform">
        <input placeholder="name, e.g. github" bind:value={mcp.name} />
        <input placeholder="command, e.g. npx" bind:value={mcp.command} />
        <input placeholder="args, e.g. -y @modelcontextprotocol/server-github" bind:value={mcp.args} />
        <input placeholder="cwd (optional)" bind:value={mcp.cwd} />
        <input placeholder="allowed tools, comma-separated (optional)" bind:value={mcp.allowed_tools} />
        <input placeholder="blocked tools, comma-separated (optional)" bind:value={mcp.blocked_tools} />
        <textarea placeholder="env, one KEY=VALUE per line (optional)" bind:value={mcp.env}></textarea>
        <button class="open" disabled={busy || !mcp.name || !mcp.command} onclick={saveMcp}>Add MCP server</button>
      </div>

      <div class="setsec">Google</div>
      <button class="setrow" onclick={openGoogle}>
        <span class="sk">Google</span>
        <span class="sv">{google == null ? '…' : google.signed_in ? ('Connected · ' + (google.email || 'account')) : 'Not connected'}</span>
        <span class="sgo">Manage →</span>
      </button>
    </div>
  {/if}

  <button class="modal-close" onclick={close}>Close</button>
</div>
