<script>
  // Inline editor for one LLM configuration — used for both create (template
  // prefill, no id) and edit (row prefill, with id). Mirrors the Profiles.svelte
  // inline-editor pattern: local state seeded from the passed config, Save/Cancel,
  // one inline error line for server 400/502 messages.
  //
  // The type select toggles which endpoint field shows: base_url for openai* /
  // anthropic, host for ollama, neither for gemini. api_key is WRITE-ONLY — blank
  // keeps the existing key (placeholder shows the hint), Clear sends "".
  import { untrack } from 'svelte'
  import { api } from '../../transport/api.js'
  import { codexOpen } from '../../store.js'
  import { getSettings } from './context.svelte.js'

  const ctx = getSettings()  // ctx.s.keys → shared provider key {set, hint} per provider

  // config: {id?, name, type, model, base_url?, host?, options?, key?, api_key?}
  //   — a template prefill has no id/key but may seed api_key (e.g. "not-needed").
  // activate: whether the save should also make this config active (decided by the
  //   parent — first-ever config, or re-saving the already-active one).
  let { config, activate = false, onSaved, onCancel } = $props()

  const TYPES = [
    { id: 'openai_responses', label: 'OpenAI · Responses' },
    { id: 'openai', label: 'OpenAI · Chat Completions' },
    { id: 'openai_subscription', label: 'OpenAI · ChatGPT subscription' },
    { id: 'anthropic', label: 'Anthropic' },
    { id: 'gemini', label: 'Gemini' },
    { id: 'ollama', label: 'Ollama' },
  ]
  // base_url applies to openai/openai_responses/anthropic; host to ollama only.
  // Subscription mode has no endpoint or key fields — both come from codex_auth.
  const usesBaseUrl = (t) => t === 'openai' || t === 'openai_responses' || t === 'anthropic'

  // Capture the prop's initial values once (this form is freshly mounted per open,
  // so initial-value capture is exactly right). untrack keeps these out of the
  // reactive graph — reading a prop inside a $state initializer otherwise warns.
  const init = untrack(() => {
    const hasOptions = !!(config.options && Object.keys(config.options).length)
    return {
      name: config.name || '',
      type: config.type || 'gemini',
      model: config.model || '',
      baseUrl: config.base_url || '',
      host: config.host || '',
      // Seed the key draft from a template's suggested api_key (e.g. "not-needed"
      // for a local server); an existing config never ships its key → empty.
      apiKey: config.api_key || '',
      // Editing a saved subscription config carries the entry view's signed_in seed;
      // a template pick has none (the $effect below refetches live for this type).
      signedIn: !!config.signed_in,
      hasOptions,
      advText: hasOptions ? JSON.stringify(config.options, null, 2) : '',
    }
  })

  let name = $state(init.name)
  let type = $state(init.type)
  let model = $state(init.model)
  let baseUrl = $state(init.baseUrl)
  let host = $state(init.host)
  let apiKey = $state(init.apiKey)
  let cleared = $state(false)         // Clear pressed → send "" to wipe the stored key
  let advOpen = $state(init.hasOptions) // Advanced JSON escape hatch (extra provider kwargs)
  let advText = $state(init.advText)

  let busy = $state(false)
  let err = $state('')
  let testing = $state(false)
  let testResult = $state(null)   // {ok, reply, latency_ms} | {ok:false, error} | null

  // ChatGPT-subscription sign-in state, for the openai_subscription form variant.
  // Seeded from the entry view's signed_in (editing a saved subscription config);
  // a template pick has no seed, so refetch live whenever this type is selected.
  // Reading $codexOpen makes this re-run when the sign-in modal closes, so the
  // "Signed in" chip below reflects a sign-in that just happened on top of this form.
  let codexSignedIn = $state(init.signedIn)
  $effect(() => {
    const modalOpen = $codexOpen
    if (type === 'openai_subscription' && !modalOpen) {
      api.codexStatus().then((s) => { codexSignedIn = !!s.signed_in }).catch(() => {})
    }
  })

  const hasKey = $derived(!!config.key?.set)
  const keyPlaceholder = $derived(hasKey ? '•••• ' + (config.key.hint || '') : 'paste key')

  function clearKey() { cleared = true; apiKey = '' }

  // Live "which key will actually be sent" line under the key field — the honest
  // answer to why a blank field can still work (shared env fallback) and where a
  // shared key will NOT go (custom endpoints get a placeholder, never the real key).
  const ENV_OF = { openai: 'OPENAI_API_KEY', openai_responses: 'OPENAI_API_KEY', anthropic: 'ANTHROPIC_API_KEY', gemini: 'GEMINI_API_KEY' }
  const PROV_OF = { openai: 'openai', openai_responses: 'openai', anthropic: 'anthropic', gemini: 'gemini' }
  const keyUsage = $derived.by(() => {
    if (type === 'openai_subscription')
      return 'Requests use your ChatGPT/Codex subscription — no API key is involved.'
    if (type === 'ollama') return 'Ollama is local — no API key is used.'
    const env = ENV_OF[type]
    const shared = ctx?.s?.keys?.[PROV_OF[type]]
    const ownKey = (apiKey !== '' && !cleared) || (hasKey && !cleared)
    if (ownKey) return `Uses this config's own key${baseUrl.trim() ? ' (sent to the custom endpoint)' : ''}. It overrides ${env}.`
    if (baseUrl.trim()) return `Custom endpoint with no key of its own — a placeholder is sent (your ${env} is never sent to non-${type.startsWith('openai') ? 'OpenAI' : 'Anthropic'} endpoints).`
    if (shared?.set) return `No key here — uses your ${env} (${shared.hint || 'set'}).`
    return `No key available — paste one above, or set ${env}.`
  })

  // The request body both Save and Test send — one source of truth so the Test
  // button exercises exactly what a save would persist. Parses the Advanced JSON
  // locally first (fast, friendly error); the server dry-constructs the provider
  // config, so a bad option comes back as its message. Returns null on a local
  // validation error (err is set). api_key is write-only: "" clears, a typed
  // string sets, blank leaves the stored key in place.
  function buildPayload() {
    let options = {}
    const text = advText.trim()
    if (advOpen && text) {
      try { options = JSON.parse(text) } catch { err = 'Advanced: not valid JSON.'; return null }
      if (!options || typeof options !== 'object' || Array.isArray(options)) {
        err = 'Advanced: must be a JSON object, e.g. {"temperature": 0.7}.'
        return null
      }
    }
    err = ''
    return {
      id: config.id,
      name: name.trim(),
      type,
      model: model.trim(),
      base_url: baseUrl.trim(),
      host: host.trim(),
      api_key: cleared ? '' : (apiKey !== '' ? apiKey : null),
      options,
    }
  }

  // useNow forces activation on top of the parent's default (first-ever config /
  // re-saving the active one) — the "Save & Use" button's one-click path.
  async function save(useNow = false) {
    const payload = buildPayload()
    if (!payload) return
    busy = true
    try {
      await api.saveLlmConfig({ ...payload, activate: activate || useNow })
      onSaved()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // Test the DRAFT as currently entered — nothing is saved; a blank key field
  // tests with the stored key (when editing), a typed one is used directly.
  async function testDraft() {
    const payload = buildPayload()
    if (!payload) return
    testing = true; testResult = null
    try {
      testResult = await api.testLlmConfigDraft(payload)
    } catch (e) {
      testResult = { ok: false, error: String(e.message || e) }
    }
    testing = false
  }
</script>

<div class="llmform">
  <div class="llmfield">
    <label for="lf-name">Name</label>
    <input id="lf-name" bind:value={name} placeholder="e.g. Gemini Flash" />
  </div>
  <div class="llmfield">
    <label for="lf-type">Type</label>
    <select id="lf-type" bind:value={type}>
      {#each TYPES as t}<option value={t.id}>{t.label}</option>{/each}
    </select>
  </div>
  <div class="llmfield">
    <label for="lf-model">Model</label>
    <input id="lf-model" bind:value={model} placeholder="e.g. gemini-3.5-flash" />
  </div>

  {#if usesBaseUrl(type)}
    <div class="llmfield">
      <label for="lf-base">Base URL <span class="llmhint">optional — point at a compatible endpoint</span></label>
      <input id="lf-base" bind:value={baseUrl} placeholder="e.g. http://localhost:8080/v1" spellcheck="false" />
    </div>
  {:else if type === 'ollama'}
    <div class="llmfield">
      <label for="lf-host">Host</label>
      <input id="lf-host" bind:value={host} placeholder="http://localhost:11434" spellcheck="false" />
    </div>
  {/if}

  {#if type === 'openai_subscription'}
    <div class="llmfield">
      <!-- A heading, NOT a <label>: a label bound to the button would forward hover
           and clicks to it (browsers propagate :hover/activation to the labeled
           control), which reads as the button lighting up from across the row. -->
      <span class="llmlabel">Sign in</span>
      <span class="llmhint">Signs requests with your ChatGPT/Codex subscription instead of an API key — unofficial, may break OpenAI's Terms of Service.</span>
      <div class="llmkeyfield">
        <button class="open" onclick={() => ctx.openCodex()}>Sign in with ChatGPT</button>
        <span class="llmtest" class:ok={codexSignedIn} class:bad={!codexSignedIn}>{codexSignedIn ? 'Signed in' : 'Not signed in'}</span>
      </div>
      <span class="llmhint">{keyUsage}</span>
    </div>
  {:else}
    <div class="llmfield">
      <label for="lf-key">API key {#if hasKey && !cleared}<span class="llmhint">leave blank to keep the current key</span>{/if}</label>
      <div class="llmkeyfield">
        <input id="lf-key" type="password" bind:value={apiKey} placeholder={keyPlaceholder} />
        {#if hasKey && !cleared}<button class="linkbtn" onclick={clearKey}>Clear key</button>{/if}
      </div>
      {#if cleared}<span class="llmhint">Key will be cleared on save.</span>{/if}
      <span class="llmhint">{keyUsage}</span>
    </div>
  {/if}

  <!-- No Advanced JSON for subscription configs: the ChatGPT backend rejects every
       tunable parameter (probed live — temperature/top_p/max_output_tokens all
       "Unsupported parameter"), and the save path strips options for this type. -->
  {#if type !== 'openai_subscription'}
    <div class="llmfield">
      <button class="linkbtn advtoggle" onclick={() => (advOpen = !advOpen)}>{advOpen ? '▾' : '▸'} Advanced (JSON)</button>
      {#if advOpen}
        <textarea
          class="llmadv" bind:value={advText} spellcheck="false"
          placeholder={'Extra AG2 provider-config settings as a JSON object, e.g.\n{\n  "temperature": 0.7\n}'}
        ></textarea>
      {/if}
    </div>
  {/if}

  {#if err}<p class="muted" style="color:#d8552f;font-size:13px;margin:0">{err}</p>{/if}
  <div class="keyrow" style="justify-content:flex-end">
    {#if testing}
      <span class="llmtest">testing…</span>
    {:else if testResult}
      <span class="llmtest" class:ok={testResult.ok} class:bad={!testResult.ok}>
        {testResult.ok ? `${testResult.reply} · ${testResult.latency_ms} ms` : testResult.error}
      </span>
    {/if}
    <button class="open" disabled={busy || testing || !model.trim()} onclick={testDraft}>Test</button>
    <button class="linkbtn" disabled={busy} onclick={onCancel}>Cancel</button>
    <button class="open" disabled={busy || testing || !name.trim() || !model.trim()} onclick={() => save()}>{busy ? 'Saving…' : 'Save'}</button>
    <!-- One-click "save and make it the active model" — hidden when the save would
         activate anyway (first config, or re-saving the already-active one). -->
    {#if !activate}
      <button class="open" disabled={busy || testing || !name.trim() || !model.trim()} onclick={() => save(true)}>Save &amp; Use</button>
    {/if}
  </div>
</div>
