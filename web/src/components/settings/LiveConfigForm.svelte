<script lang="ts">
  // Inline editor for one LIVE (voice) configuration — the spoken counterpart of
  // LlmConfigForm, trimmed to what realtime voice needs: Name, Provider (the fixed
  // 2-provider registry), Model (realtime model, defaults to the provider's), and a
  // Secret picker with a paste-to-create shortcut. No base_url/host/subscription/
  // Advanced-JSON. Voice is NOT set here — it's chosen from the config row's
  // "Change voice" picker (a new config gets the provider's default voice until then).
  import { onMount, untrack } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { getSettings } from './context.svelte.ts'
  import type { LiveConfigSeed } from '../../lib/live.ts'
  import { PROVIDER_LABEL } from '../../lib/providerLabels.ts'
  import { secretsStore, loadSecrets, createOrSnap } from '../../lib/secrets.ts'
  import { autoSecretName, sortForProvider } from '../../lib/secretsUtil.ts'
  import { errText } from '../../lib/errors.ts'
  import type { LiveConfigDraft } from '../../transport/api/llm.ts'
  import type { LiveProvider, PingResult } from '../../schemas/index.ts'

  const ctx = getSettings()  // ctx.s.keys → shared provider key {set, hint} per provider

  // config: a saved live config, or a provider template prefill (no id/secret).
  // providers: the server catalog (for the model placeholder). activate: whether the
  // save should also make this config active (parent-decided).
  type Props = {
    config: LiveConfigSeed
    providers?: LiveProvider[]
    activate?: boolean
    onSaved: () => void
    onCancel: () => void
  }
  let { config, providers = [], activate = false, onSaved, onCancel }: Props = $props()

  const PROVIDERS = ['gemini', 'openai']

  const init = untrack(() => ({
    name: config.name || '',
    provider: config.provider || 'openai',
    model: config.model || '',
    // A dangling reference (secret deleted) starts the picker at "none".
    secretId: config.secret_missing ? '' : (config.secret_id || ''),
  }))

  let name = $state(init.name)
  let provider = $state(init.provider)
  let model = $state(init.model)
  let secretId = $state(init.secretId)  // '' = no secret (default/env fallback)
  let pastedKey = $state('')            // non-empty → create-or-snap a Secret on save

  onMount(loadSecrets)

  let busy = $state(false)
  let err = $state('')
  let testing = $state(false)
  // The draft Test outcome: the PONG round-trip, or the failure message.
  let testResult = $state<PingResult | { ok: false; error: string } | null>(null)

  const defaultModel = $derived(providers.find((p) => p.name === provider)?.default_model || '')
  const pickerSecrets = $derived(sortForProvider($secretsStore.secrets, provider))

  // Live "which key will actually be sent" line — the honest answer to why an
  // empty selection can still work (the provider default / env key fallback).
  const ENV_OF: Record<string, string | undefined> = { openai: 'OPENAI_API_KEY', gemini: 'GEMINI_API_KEY' }
  const keyUsage = $derived.by(() => {
    const env = ENV_OF[provider]
    const shared = ctx?.s?.keys?.[provider]
    if (pastedKey.trim()) return 'A new Secret will be created from this key on save (rename it later in Settings → Secrets).'
    if (secretId) {
      const s = $secretsStore.secrets.find((x) => x.id === secretId)
      return s
        ? `Uses the "${s.name}" secret. It overrides ${env}.`
        : 'The referenced secret was deleted — falls back to the provider default or env key.'
    }
    if (shared?.set) return `No secret selected — uses your ${env} (${shared.hint || 'set'}).`
    return `No key available — pick or paste one above, or set ${env}.`
  })

  // The request body Save and Test share — model blank is allowed (the server fills
  // the provider default). api_key rides only the draft-test call (a pasted key,
  // used directly); save mints a Secret from it instead.
  function buildPayload(): LiveConfigDraft {
    err = ''
    return {
      id: config.id,
      name: name.trim(),
      provider,
      model: model.trim(),
      secret_id: secretId,
      api_key: pastedKey.trim() || null,
    }
  }

  async function save(useNow = false) {
    const payload = buildPayload()
    busy = true
    try {
      if (pastedKey.trim()) {
        // Paste-to-create: mint (or snap to) a Secret, then reference it.
        const s = await createOrSnap({ name: autoSecretName(name, pastedKey), value: pastedKey.trim() })
        payload.secret_id = s.id
        secretId = s.id
        pastedKey = ''
        loadSecrets()
      }
      delete payload.api_key  // never persisted; Secrets carry the key
      await api.saveLiveConfig({ ...payload, activate: activate || useNow })
      onSaved()
    } catch (e) { err = errText(e) }
    busy = false
  }

  async function testDraft() {
    const payload = buildPayload()
    testing = true; testResult = null
    try {
      testResult = await api.testLiveConfigDraft(payload)
    } catch (e) {
      testResult = { ok: false, error: errText(e) }
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
    <label for="vf-secret">Secret <span class="llmhint">a reusable API key — manage in Settings → Secrets</span></label>
    <div class="llmkeyfield">
      <select id="vf-secret" bind:value={secretId} disabled={!!pastedKey.trim()}>
        <option value="">No secret — provider default / env key</option>
        {#each pickerSecrets as s (s.id)}
          <option value={s.id}>{s.name} {s.hint}{s.default ? ' · default' : ''}</option>
        {/each}
      </select>
    </div>
    <div class="llmkeyfield">
      <input id="vf-key" type="password" bind:value={pastedKey} placeholder="…or paste a new key to create a secret" />
    </div>
    {#if config.secret_missing}<span class="llmhint">This model referenced a deleted secret.</span>{/if}
    <span class="llmhint">{keyUsage}</span>
  </div>

  {#if err}<p class="muted" style="color:var(--danger);font-size:13px;margin:0">{err}</p>{/if}
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
