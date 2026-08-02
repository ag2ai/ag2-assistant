<script>
  // Inline editor for one LLM configuration — used for both create (template
  // prefill, no id) and edit (row prefill, with id). Mirrors the Profiles.svelte
  // inline-editor pattern: local state seeded from the passed config, Save/Cancel,
  // one inline error line for server 400/502 messages.
  //
  // The type select toggles which endpoint field shows: base_url for openai* /
  // anthropic, host for ollama, neither for gemini. The key field is a Secret
  // picker plus a paste-to-create shortcut (a pasted key mints a value-unique
  // Secret on save; api_key rides only the draft-test call).
  import { onMount, untrack } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { codexOpen } from '../../store.ts'
  import { getSettings } from './context.svelte.js'
  import { secretsStore, loadSecrets, createOrSnap } from '../../lib/secrets.js'
  import { autoSecretName, sortForProvider } from '../../lib/secretsUtil.js'
  import { splitModelId, joinModelId, effortLabel, groupModels } from '../../lib/codexModels.js'

  const ctx = getSettings()  // ctx.s.keys → shared provider key {set, hint} per provider

  // config: {id?, name, type, model, base_url?, host?, options?, secret_id?,
  //   secret?, secret_missing?} — a template prefill has no id/secret.
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
    { id: 'claude_code', label: 'Claude Code · CLI login' },
    { id: 'codex', label: 'Codex · CLI login' },
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
      // A dangling reference (secret deleted) starts the picker at "none".
      secretId: config.secret_missing ? '' : (config.secret_id || ''),
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
  let secretId = $state(init.secretId)  // '' = no secret (default/env fallback)
  let pastedKey = $state('')            // non-empty → create-or-snap a Secret on save
  let advOpen = $state(init.hasOptions) // Advanced JSON escape hatch (extra provider kwargs)
  let advText = $state(init.advText)

  onMount(loadSecrets)

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

  // Live "which key will actually be sent" line under the picker — the honest
  // answer to why an empty selection can still work (default/env fallback) and
  // where a shared key will NOT go (custom endpoints get a placeholder).
  const ENV_OF = { openai: 'OPENAI_API_KEY', openai_responses: 'OPENAI_API_KEY', anthropic: 'ANTHROPIC_API_KEY', gemini: 'GEMINI_API_KEY' }
  const PROV_OF = { openai: 'openai', openai_responses: 'openai', anthropic: 'anthropic', gemini: 'gemini' }
  const pickerSecrets = $derived(sortForProvider($secretsStore.secrets, PROV_OF[type] || ''))
  const keyUsage = $derived.by(() => {
    if (type === 'openai_subscription')
      return 'Requests use your ChatGPT/Codex subscription — no API key is involved.'
    if (type === 'claude_code')
      return 'Runs on your Claude Code CLI login (claude-agent-acp) — no API key is involved.'
    if (type === 'codex')
      return 'Runs on your Codex CLI login (codex-acp) — no API key is involved.'
    if (type === 'ollama') return 'Ollama is local — no API key is used.'
    const env = ENV_OF[type]
    const shared = ctx?.s?.keys?.[PROV_OF[type]]
    if (pastedKey.trim()) return 'A new Secret will be created from this key on save (rename it later in Settings → Secrets).'
    if (secretId) {
      const s = $secretsStore.secrets.find((x) => x.id === secretId)
      return s
        ? `Uses the "${s.name}" secret${baseUrl.trim() ? ' (sent to the custom endpoint)' : ''}. It overrides ${env}.`
        : 'The referenced secret was deleted — falls back to the provider default or env key.'
    }
    if (baseUrl.trim()) return `Custom endpoint with no secret — a placeholder is sent (your ${env} is never sent to non-${type.startsWith('openai') ? 'OpenAI' : 'Anthropic'} endpoints).`
    if (shared?.set) return `No secret selected — uses your ${env} (${shared.hint || 'set'}).`
    return `No key available — pick or paste one above, or set ${env}.`
  })

  // Empty model is valid for the CLI-login types only (= the CLI's own default)
  // — every other type requires an explicit model name.
  const modelOptional = $derived(type === 'codex' || type === 'claude_code')

  // Model names belong to their provider ("haiku" means nothing to Codex), so
  // switching type clears the model instead of carrying a stale name into the
  // new picker. Exception: the two OpenAI API surfaces are the same catalog, so
  // switching between them keeps the model. Only a user-driven change resets —
  // the initial value from an edited/prefilled entry is never touched.
  const MODEL_FAMILY = { openai: 'openai', openai_responses: 'openai' }
  const modelFamily = (t) => MODEL_FAMILY[t] || t
  function changeType(next) {
    if (next === type) return
    if (modelFamily(next) !== modelFamily(type)) model = ''
    type = next
  }

  // ACP model picker: the adapter's live catalog, fetched once per agent when its
  // type is first selected. missing key = not asked yet, 'loading' = in flight,
  // else {models, current, reason} — reason names WHY an empty catalog is empty
  // (adapter_missing / bridge / probe_failed) so the form says it out loud instead
  // of quietly offering a text box. The single source of truth stays the `model`
  // string — the selects just edit it. Codex ids decompose into two selects
  // (family[effort] = model + reasoning); claude ids do NOT (the bracket there is
  // part of the model preference, e.g. "opus[1m]" = 1M context) — one flat select.
  const acpAgent = $derived(type === 'codex' ? 'codex' : type === 'claude_code' ? 'claude' : '')
  /** @type {Record<string, 'loading' | import('../../schemas/index.ts').CodingCatalog>} */
  let catalogs = $state({})
  function fetchCatalog(agent, refresh = false) {
    catalogs[agent] = 'loading'
    api.codingModels(agent, refresh)
      .then((r) => { catalogs[agent] = { models: r.models || [], current: r.current || '', reason: r.reason || '' } })
      .catch(() => { catalogs[agent] = { models: [], current: '', reason: 'probe_failed' } })
  }
  $effect(() => {
    if (acpAgent && catalogs[acpAgent] === undefined) fetchCatalog(acpAgent)
  })
  const acpLoading = $derived(!!acpAgent && catalogs[acpAgent] === 'loading')
  // The typeof guard is what separates the 'loading' sentinel from a loaded
  // catalog — narrowing the union here rather than leaning on acpLoading, which
  // reads the same slot but can't tell the type checker anything about it.
  const acpState = $derived(typeof catalogs[acpAgent] === 'object' ? catalogs[acpAgent] : null)
  const acpCatalog = $derived(acpState?.models ?? [])
  // The adapter's own current selection labels the "CLI default" row, so leaving
  // the model empty is a legible choice rather than a blind one.
  const defaultLabel = $derived(acpState?.current ? `CLI default (${acpState.current})` : 'CLI default')
  const ADAPTER_PKG = { claude: '@agentclientprotocol/claude-agent-acp', codex: '@agentclientprotocol/codex-acp' }
  const acpNote = $derived.by(() => {
    if (!acpAgent || acpLoading || acpCatalog.length) return ''
    if (acpState?.reason === 'adapter_missing')
      return `No model list: the ACP adapter isn't installed (npm i -g ${ADAPTER_PKG[acpAgent]}). Leave the field empty to use the CLI's own model.`
    if (acpState?.reason === 'bridge')
      return "No model list in bridge mode — the CLI runs on the host, out of reach of this container. Leave the field empty to use the CLI's own model."
    if (acpState?.reason)
      return "Couldn't read the CLI's model list. Leave the field empty to use the CLI's own model, or type a name it accepts."
    return ''
  })

  // claude: flat options. A saved model that fell out of the catalog stays
  // visible as an extra option (the adapter's own "default" row is dropped
  // server-side — our "CLI default" entry, an empty model, is that case).
  const claudeOptions = $derived.by(() => {
    if (model && !acpCatalog.some((m) => m.id === model)) {
      return [{ id: model, name: model, description: '' }, ...acpCatalog]
    }
    return acpCatalog
  })

  // codex: grouped two-select decomposition (see lib/codexModels.js).
  const codexGroups = $derived(acpAgent === 'codex' ? groupModels(acpCatalog) : [])
  const codexPick = $derived(splitModelId(model))
  const codexFamilies = $derived.by(() => {
    if (!codexPick.family || codexGroups.some((g) => g.family === codexPick.family)) return codexGroups
    return [{ family: codexPick.family, label: codexPick.family, efforts: codexPick.effort ? [{ value: codexPick.effort, label: effortLabel(codexPick.effort) }] : [] }, ...codexGroups]
  })
  const codexEfforts = $derived(codexFamilies.find((g) => g.family === codexPick.family)?.efforts || [])
  function pickCodexFamily(family) {
    // Keep the effort when the new family offers it; otherwise fall back to the
    // family's own default (no bracket → the adapter's default tier).
    const efforts = codexFamilies.find((g) => g.family === family)?.efforts || []
    const keep = efforts.some((e) => e.value === codexPick.effort) ? codexPick.effort : ''
    model = joinModelId(family, keep)
  }

  // The request body both Save and Test send — one source of truth so the Test
  // button exercises exactly what a save would persist. Parses the Advanced JSON
  // locally first (fast, friendly error); the server dry-constructs the provider
  // config, so a bad option comes back as its message. Returns null on a local
  // validation error (err is set). api_key rides only the draft-test call (a
  // pasted key, used directly); save mints a Secret from it instead.
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
      secret_id: secretId,
      api_key: pastedKey.trim() || null,  // draft-test only; save creates a Secret first
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
      if (pastedKey.trim()) {
        // Paste-to-create: mint (or snap to) a Secret, then reference it.
        const s = await createOrSnap({ name: autoSecretName(name, pastedKey), value: pastedKey.trim() })
        payload.secret_id = s.id
        secretId = s.id
        pastedKey = ''
        loadSecrets()
      }
      delete payload.api_key  // never persisted; Secrets carry the key
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
    <select id="lf-type" value={type} onchange={(e) => changeType(e.currentTarget.value)}>
      {#each TYPES as t}<option value={t.id}>{t.label}</option>{/each}
    </select>
  </div>
  {#if acpLoading}
    <!-- Wait for the adapter's real catalog rather than offering a text box with
         invented example names: the CLI is the authority on what models exist. -->
    <div class="llmfield">
      <label for="lf-model-loading">Model</label>
      <select id="lf-model-loading" disabled><option>Reading the CLI's model list…</option></select>
    </div>
  {:else if type === 'codex' && codexGroups.length}
    <!-- The adapter's live catalog, shown the way Codex's own picker does: model
         and reasoning as separate selects. The joined family[effort] id is what
         gets stored — the free-text fallback below edits the same string. -->
    <div class="llmfield">
      <label for="lf-model-family">Model</label>
      <select id="lf-model-family" value={codexPick.family} onchange={(e) => pickCodexFamily(e.currentTarget.value)}>
        <option value="">{defaultLabel}</option>
        {#each codexFamilies as g (g.family)}
          <option value={g.family}>{g.label}</option>
        {/each}
      </select>
    </div>
    {#if codexEfforts.length}
      <div class="llmfield">
        <label for="lf-model-effort">Reasoning</label>
        <select id="lf-model-effort" value={codexPick.effort} onchange={(e) => (model = joinModelId(codexPick.family, e.currentTarget.value))}>
          <option value="">Default</option>
          {#each codexEfforts as e (e.value)}
            <option value={e.value}>{e.label}</option>
          {/each}
        </select>
      </div>
    {/if}
  {:else if type === 'claude_code' && claudeOptions.length}
    <!-- Claude Code's catalog values ride ANTHROPIC_MODEL verbatim ("opus[1m]",
         "sonnet", …) — one flat select, no decomposition. -->
    <div class="llmfield">
      <label for="lf-model-claude">Model</label>
      <select id="lf-model-claude" bind:value={model}>
        <option value="">{defaultLabel}</option>
        {#each claudeOptions as m (m.id)}
          <option value={m.id} title={m.description}>{m.name}</option>
        {/each}
      </select>
    </div>
  {:else}
    <div class="llmfield">
      <label for="lf-model">Model</label>
      <!-- No catalog: for the CLI types the placeholder stays empty on purpose (an
           invented example is worse than none — acpNote explains what happened). -->
      <input id="lf-model" bind:value={model} placeholder={acpAgent ? '' : 'e.g. gemini-3.6-flash'} />
    </div>
  {/if}
  {#if acpAgent && !acpLoading}
    <!-- One row for both states: why there's no list (if so) plus a manual re-probe,
         for when the CLI or its adapter was installed/upgraded since the last read
         (the server caches the catalog for a few minutes). -->
    <div class="llmfield">
      <span class="llmhint">
        {#if acpNote}{acpNote} {/if}
        <button class="linkbtn" onclick={() => fetchCatalog(acpAgent, true)}>Re-read the model list</button>
      </span>
    </div>
  {/if}

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
  {:else if type === 'claude_code' || type === 'codex'}
    <!-- No endpoint or key fields: auth is the CLI's own on-disk login, and the ACP
         adapter is found on PATH (or via the Docker host bridge). -->
    <div class="llmfield">
      <span class="llmlabel">Authentication</span>
      <span class="llmhint">{keyUsage}</span>
      <!-- Requirements only; the model list comes from the adapter itself, so no
           example model names are spelled out here (they rot with every release). -->
      {#if type === 'claude_code'}
        <span class="llmhint">Requires the <code>claude-agent-acp</code> adapter on PATH (<code>npm i -g @agentclientprotocol/claude-agent-acp</code>) and a logged-in Claude Code CLI.</span>
      {:else}
        <span class="llmhint">Requires the <code>codex-acp</code> adapter on PATH (<code>npm i -g @agentclientprotocol/codex-acp</code>) and a logged-in Codex CLI.</span>
      {/if}
    </div>
  {:else}
    <div class="llmfield">
      <label for="lf-secret">Secret <span class="llmhint">a reusable API key — manage in Settings → Secrets</span></label>
      <div class="llmkeyfield">
        <select id="lf-secret" bind:value={secretId} disabled={!!pastedKey.trim()}>
          <option value="">No secret — provider default / env key</option>
          {#each pickerSecrets as s (s.id)}
            <option value={s.id}>{s.name} {s.hint}{s.default ? ' · default' : ''}</option>
          {/each}
        </select>
      </div>
      <div class="llmkeyfield">
        <input id="lf-key" type="password" bind:value={pastedKey} placeholder="…or paste a new key to create a secret" />
      </div>
      {#if config.secret_missing}<span class="llmhint">This model referenced a deleted secret.</span>{/if}
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

  {#if err}<p class="muted" style="color:var(--danger);font-size:13px;margin:0">{err}</p>{/if}
  <div class="keyrow" style="justify-content:flex-end">
    {#if testing}
      <span class="llmtest">testing…</span>
    {:else if testResult}
      <span class="llmtest" class:ok={testResult.ok} class:bad={!testResult.ok}>
        {testResult.ok ? `${testResult.reply} · ${testResult.latency_ms} ms` : testResult.error}
      </span>
    {/if}
    <button class="open" disabled={busy || testing || (!model.trim() && !modelOptional)} onclick={testDraft}>Test</button>
    <button class="linkbtn" disabled={busy} onclick={onCancel}>Cancel</button>
    <button class="open" disabled={busy || testing || !name.trim() || (!model.trim() && !modelOptional)} onclick={() => save()}>{busy ? 'Saving…' : 'Save'}</button>
    <!-- One-click "save and make it the active model" — hidden when the save would
         activate anyway (first config, or re-saving the already-active one). -->
    {#if !activate}
      <button class="open" disabled={busy || testing || !name.trim() || (!model.trim() && !modelOptional)} onclick={() => save(true)}>Save &amp; Use</button>
    {/if}
  </div>
</div>
