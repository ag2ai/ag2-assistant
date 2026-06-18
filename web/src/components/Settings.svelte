<script>
  import { onMount } from 'svelte'
  import { settingsOpen, voicePickerOpen, googleOpen } from '../store.js'
  import { api } from '../transport/api.js'

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
  let err = $state('')
  let busy = $state(false)

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

  const close = () => ($settingsOpen = false)
  const openVoice = () => { $settingsOpen = false; $voicePickerOpen = true }
  const openGoogle = () => { $settingsOpen = false; $googleOpen = true }
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal settings">
  <h2>Settings</h2>
  {#if err}<p class="muted" style="color:#d8552f">{err}</p>{/if}

  {#if !s}
    <p class="muted">Loading…</p>
  {:else}
    <div class="setscroll">
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
