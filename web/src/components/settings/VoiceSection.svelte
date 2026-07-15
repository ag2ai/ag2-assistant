<script>
  // Settings → Models → Live: the voice provider (per-profile) plus the SHARED
  // Gemini/OpenAI provider-key rows. Voice runs on Gemini or OpenAI directly and
  // reads those keys from the environment, so these are deliberately the same
  // install-wide key slots the model configs use — not a separate voice-only secret.
  // Rendered as the "Live" group of ModelsPage (no longer its own nav page).
  import { getSettings } from './context.svelte.js'
  import { api } from '../../transport/api.js'

  const ctx = getSettings()
  const LABEL = { gemini: 'Gemini', openai: 'OpenAI' }
  const VOICE_PROVIDERS = ['gemini', 'openai']

  const saveKey = (p) => ctx.run(() => api.setKey(p, ctx.drafts[p] || '').then(() => { ctx.drafts[p] = '' }))
  const clearKey = (p) => ctx.run(() => api.setKey(p, ''))
  const saveVoiceProvider = (p) => ctx.run(() => api.setVoiceProvider(p))
</script>

<div class="setsec">Voice provider</div>
{#if VOICE_PROVIDERS.some((p) => ctx.s.voice_available?.[p])}
  <div class="keyrow">
    <select value={ctx.s.voice_provider} onchange={(e) => saveVoiceProvider(e.target.value)}>
      {#each VOICE_PROVIDERS as p}
        {@const ok = ctx.s.voice_available?.[p]}
        <option value={p} disabled={!ok}>{LABEL[p]}{ok ? '' : ' (no key)'}</option>
      {/each}
    </select>
    <button class="open" onclick={ctx.openVoice}>Change voice</button>
  </div>
{:else}
  <p class="muted" style="font-size:13px">Add an OpenAI or Gemini key below to enable voice.</p>
{/if}

<div class="setsec">Provider keys <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<p class="muted" style="font-size:13px;margin:0">Voice runs on Gemini or OpenAI directly — add the key for the provider you pick.</p>
{#each VOICE_PROVIDERS as p}
  <div class="keyrow">
    <span class="kp">{LABEL[p]}</span>
    <input type="password" placeholder={ctx.s.keys[p]?.set ? '•••• ' + ctx.s.keys[p].hint : 'paste key'} bind:value={ctx.drafts[p]} />
    <button class="open" disabled={ctx.busy} onclick={() => saveKey(p)}>Save</button>
    {#if ctx.s.keys[p]?.set}<button class="linkbtn" disabled={ctx.busy} onclick={() => clearKey(p)}>Clear</button>{/if}
  </div>
{/each}
