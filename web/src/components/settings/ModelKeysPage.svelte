<script>
  // Settings → Model & Keys: install-wide API keys, the assistant model, the voice provider.
  import { getSettings } from './context.svelte.js'
  import { api } from '../../transport/api.js'

  const ctx = getSettings()

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

  const saveKey = (p) => ctx.run(() => api.setKey(p, ctx.drafts[p] || '').then(() => { ctx.drafts[p] = '' }))
  const clearKey = (p) => ctx.run(() => api.setKey(p, ''))
  const saveOllama = () => ctx.run(() => api.setKey('ollama', ctx.drafts.ollama || ''))
  const saveLlm = (p) => ctx.run(() => api.setLlm(p, ctx.model))
  const saveVoiceProvider = (p) => ctx.run(() => api.setVoiceProvider(p))
</script>

<div class="setsec">API keys <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
{#each KEY_ROWS as k}
  <div class="keyrow">
    <span class="kp">{k.label}</span>
    <input type="password" placeholder={ctx.s.keys[k.id]?.set ? '•••• ' + ctx.s.keys[k.id].hint : k.ph} bind:value={ctx.drafts[k.id]} />
    <button class="open" disabled={ctx.busy} onclick={() => saveKey(k.id)}>Save</button>
    {#if ctx.s.keys[k.id]?.set}<button class="linkbtn" disabled={ctx.busy} onclick={() => clearKey(k.id)}>Clear</button>{/if}
  </div>
{/each}
<div class="keyrow">
  <span class="kp">Ollama</span>
  <input type="text" placeholder="http://localhost:11434" bind:value={ctx.drafts.ollama} />
  <button class="open" disabled={ctx.busy} onclick={saveOllama}>Save</button>
</div>

<div class="setsec">Assistant model</div>
<div class="keyrow">
  <select bind:value={ctx.s.assistant.provider}>
    {#each Object.keys(PROVIDER_LABEL) as p}
      <option value={p} disabled={!ctx.s.available[p]}>{PROVIDER_LABEL[p]}{ctx.s.available[p] ? '' : ' (no key)'}</option>
    {/each}
  </select>
  <input type="text" placeholder="model, e.g. gemini-3.5-flash" bind:value={ctx.model} />
  <button class="open" disabled={ctx.busy} onclick={() => saveLlm(ctx.s.assistant.provider)}>Save</button>
</div>

<div class="setsec">Voice</div>
{#if VOICE_PROVIDERS.some((p) => ctx.s.available[p])}
  <div class="keyrow">
    <select value={ctx.s.voice_provider} onchange={(e) => saveVoiceProvider(e.target.value)}>
      {#each VOICE_PROVIDERS as p}
        <option value={p} disabled={!ctx.s.available[p]}>{PROVIDER_LABEL[p]}{ctx.s.available[p] ? '' : ' (no key)'}</option>
      {/each}
    </select>
    <button class="open" onclick={ctx.openVoice}>Change voice</button>
  </div>
{:else}
  <p class="muted" style="font-size:13px">Add an OpenAI or Gemini key above to enable voice.</p>
{/if}
