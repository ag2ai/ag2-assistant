<script>
  // Inline editor for one LIVE (voice) configuration — the spoken counterpart of
  // LlmConfigForm, trimmed to what realtime voice needs: Name, Provider (the fixed
  // 2-provider registry), Model (realtime model, defaults to the provider's), and a
  // write-only API key. No base_url/host/subscription/Advanced-JSON. Voice is NOT set
  // here — it's chosen from the config row's "Change voice" picker (a new config gets
  // the provider's default voice until then).
  import { untrack } from 'svelte'
  import { api } from '../../transport/api.js'
  import { getSettings } from './context.svelte.js'
  import { PROVIDER_LABEL } from '../../lib/live.js'

  const ctx = getSettings()  // ctx.s.keys → shared provider key {set, hint} per provider

  // config: {id?, name, provider, model, key?, api_key?}. providers: the server catalog
  // [{name, default_model, default_voice}] (for the model placeholder). activate: whether
  // the save should also make this config active (parent-decided).
  let { config, providers = [], activate = false, onSaved, onCancel } = $props()

  const PROVIDERS = ['gemini', 'openai']

  const init = untrack(() => ({
    name: config.name || '',
    provider: config.provider || 'openai',
    model: config.model || '',
    apiKey: config.api_key || '',
  }))

  let name = $state(init.name)
  let provider = $state(init.provider)
  let model = $state(init.model)
  let apiKey = $state(init.apiKey)
  let cleared = $state(false)   // Clear pressed → send "" to wipe the stored key

  let busy = $state(false)
  let err = $state('')
  let testing = $state(false)
  let testResult = $state(null)

  const defaultModel = $derived(providers.find((p) => p.name === provider)?.default_model || '')
  const hasKey = $derived(!!config.key?.set)
  const keyPlaceholder = $derived(hasKey ? '•••• ' + (config.key.hint || '') : 'paste key')

  function clearKey() { cleared = true; apiKey = '' }

  // Live "which key will actually be sent" line — the honest answer to why a blank
  // field can still work (the shared provider key fallback).
  const ENV_OF = { openai: 'OPENAI_API_KEY', gemini: 'GEMINI_API_KEY' }
  const keyUsage = $derived.by(() => {
    const env = ENV_OF[provider]
    const shared = ctx?.s?.keys?.[provider]
    const ownKey = (apiKey !== '' && !cleared) || (hasKey && !cleared)
    if (ownKey) return `Uses this config's own key. It overrides ${env}.`
    if (shared?.set) return `No key here — uses your ${env} (${shared.hint || 'set'}).`
    return `No key available — paste one above, or set ${env}.`
  })

  // The request body Save and Test share — model blank is allowed (the server fills
  // the provider default). api_key is write-only: "" clears, a value sets, blank keeps.
  function buildPayload() {
    err = ''
    return {
      id: config.id,
      name: name.trim(),
      provider,
      model: model.trim(),
      api_key: cleared ? '' : (apiKey !== '' ? apiKey : null),
    }
  }

  async function save(useNow = false) {
    const payload = buildPayload()
    busy = true
    try {
      await api.saveLiveConfig({ ...payload, activate: activate || useNow })
      onSaved()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  async function testDraft() {
    const payload = buildPayload()
    testing = true; testResult = null
    try {
      testResult = await api.testLiveConfigDraft(payload)
    } catch (e) {
      testResult = { ok: false, error: String(e.message || e) }
    }
    testing = false
  }
</script>

<div class="llmform">
  <div class="llmfield">
    <label for="vf-name">Name</label>
    <input id="vf-name" bind:value={name} placeholder="e.g. OpenAI Live" />
  </div>
  <div class="llmfield">
    <label for="vf-provider">Provider</label>
    <select id="vf-provider" bind:value={provider}>
      {#each PROVIDERS as p}<option value={p}>{PROVIDER_LABEL[p]}</option>{/each}
    </select>
  </div>
  <div class="llmfield">
    <label for="vf-model">Model <span class="llmhint">realtime model — leave blank for the provider default</span></label>
    <input id="vf-model" bind:value={model} placeholder={defaultModel ? 'e.g. ' + defaultModel : 'realtime model'} spellcheck="false" />
  </div>

  <div class="llmfield">
    <label for="vf-key">API key {#if hasKey && !cleared}<span class="llmhint">leave blank to keep the current key</span>{/if}</label>
    <div class="llmkeyfield">
      <input id="vf-key" type="password" bind:value={apiKey} placeholder={keyPlaceholder} />
      {#if hasKey && !cleared}<button class="linkbtn" onclick={clearKey}>Clear key</button>{/if}
    </div>
    {#if cleared}<span class="llmhint">Key will be cleared on save.</span>{/if}
    <span class="llmhint">{keyUsage}</span>
  </div>

  {#if err}<p class="muted" style="color:#d8552f;font-size:13px;margin:0">{err}</p>{/if}
  <div class="keyrow" style="justify-content:flex-end">
    {#if testing}
      <span class="llmtest">testing…</span>
    {:else if testResult}
      <span class="llmtest" class:ok={testResult.ok} class:bad={!testResult.ok}>
        {testResult.ok ? `${testResult.reply} · ${testResult.latency_ms} ms` : testResult.error}
      </span>
    {/if}
    <button class="open" disabled={busy || testing} onclick={testDraft}>Test</button>
    <button class="linkbtn" disabled={busy} onclick={onCancel}>Cancel</button>
    <button class="open" disabled={busy || testing || !name.trim()} onclick={() => save()}>{busy ? 'Saving…' : 'Save'}</button>
    {#if !activate}
      <button class="open" disabled={busy || testing || !name.trim()} onclick={() => save(true)}>Save &amp; Use</button>
    {/if}
  </div>
</div>
