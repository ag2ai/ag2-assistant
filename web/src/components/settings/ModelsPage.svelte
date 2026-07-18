<script>
  // Settings → Models: two stacked groups.
  //   • Text — the install-wide list of named LLM configurations and the one active
  //     selection (LLM is common across every profile — no per-profile override, no
  //     fallback). Each config has a type, type-specific fields, an optional secret
  //     per-config key, a provider logo, a one-click real-PONG Test, and an explicit
  //     Use. Replaces the old Model & Keys page.
  //   • Live — the voice provider + shared provider keys, rendered by <VoiceSection/>
  //     (formerly its own "Voice" nav page, folded in here).
  //
  // The config list lives in the shared `llmConfigs` store (lib/llm.js), so edits
  // here (add / rename / Use) show up live in the composer's ModelSwitcher without
  // a reload. Local UI state (tests, editing, adding, busy, err) stays private.
  // Test uses the McpServers per-row health-map pattern (a `tests` map keyed by
  // config id: {testing} → green PONG/latency or red error).
  // See docs/adr/0004-shared-llm-config-store.md.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import LlmConfigForm from './LlmConfigForm.svelte'
  import VoiceSection from './VoiceSection.svelte'
  import Icon from '../Icon.svelte'
  import { LOGO, TYPE_LABEL, llmConfigs, loadLlmConfigs } from '../../lib/llm.js'
  // One-click starting points. Picking a card opens the editor prefilled — the
  // two-field local-server case is one click plus a model name.
  const TEMPLATES = [
    { name: 'Gemini', type: 'gemini', model: 'gemini-3.5-flash', blurb: 'Google Gemini' },
    { name: 'OpenAI', type: 'openai_responses', model: 'gpt-5.6-terra', blurb: 'Responses API' },
    { name: 'OpenAI · Chat Completions', type: 'openai', model: 'gpt-5.6-terra', blurb: 'Chat Completions API' },
    {
      name: 'OpenAI · ChatGPT subscription',
      card: 'OpenAI · Sign in with ChatGPT',
      type: 'openai_subscription', model: 'gpt-5.6-terra',
      blurb: 'Use your ChatGPT/Codex subscription instead of an API key — unofficial, may break OpenAI ToS',
    },
    { name: 'Anthropic', type: 'anthropic', model: 'claude-opus-4-8', blurb: 'Claude' },
    { name: 'Ollama', type: 'ollama', model: 'llama3.2', host: 'http://localhost:11434', blurb: 'Local Ollama' },
    {
      name: 'Local server', card: 'Local server — llama.cpp / vLLM / LM Studio',
      type: 'openai', model: '', base_url: 'http://localhost:8080/v1',
      blurb: 'OpenAI-compatible local server — just set the model name',
    },
    {
      name: 'Anthropic-compatible', card: 'Anthropic-compatible — MiniMax & proxies',
      type: 'anthropic', model: 'MiniMax-M2.5', base_url: 'https://api.minimax.io/anthropic',
      blurb: 'Anthropic-API server like MiniMax cloud — set the model, endpoint and key',
    },
  ]

  const configs = $derived($llmConfigs.configs)
  const envOverride = $derived($llmConfigs.envOverride)
  let tests = $state({})       // config id -> {testing} | {ok, reply, latency_ms} | {ok:false, error}
  let busy = $state(false)
  let err = $state('')

  let editing = $state(null)   // config/template being edited in the inline form (null = closed)
  let adding = $state(false)   // template card grid showing

  onMount(reload)

  // Thin wrapper: refresh the shared store and surface any failure in this page's err.
  async function reload() {
    try { await loadLlmConfigs() } catch (e) { err = String(e.message || e) }
  }

  // Test = per-row health map, exactly like McpServers.check.
  async function test(c) {
    tests = { ...tests, [c.id]: { testing: true } }
    try {
      tests = { ...tests, [c.id]: { ok: true, ...(await api.testLlmConfig(c.id)) } }
    } catch (e) {
      tests = { ...tests, [c.id]: { ok: false, error: String(e.message || e) } }
    }
  }

  async function use(c) {
    err = ''; busy = true
    try { await api.useLlmConfig(c.id); await reload() } catch (e) { err = String(e.message || e) }
    busy = false
  }

  async function remove(c) {
    err = ''; busy = true
    try {
      await api.deleteLlmConfig(c.id)
      const { [c.id]: _gone, ...rest } = tests
      tests = rest
      await reload()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // Open the editor: a template (no id → create) or an existing row (edit).
  function pickTemplate(t) { adding = false; editing = { ...t } }
  function edit(c) { adding = false; editing = { ...c } }

  // The save should activate when it's the first-ever config (empty store), or when
  // re-saving the already-active config (keep it active). Otherwise the explicit Use
  // button owns activation.
  const activateOnSave = $derived(editing?.id ? !!editing.active : configs.length === 0)

  async function onSaved() { editing = null; await reload() }

  const endpoint = (c) => (c.type === 'ollama' ? c.host : c.base_url) || ''

  // Honest key chip: name the key an actual call would send (key_source from the
  // server), not just whether a per-config key exists — a keyless Gemini config
  // still works via the shared env key, and a base_url config needs none at all.
  // Hints already carry the "…" ellipsis (e.g. "…abcd").
  function keyChip(c) {
    if (c.key_source === 'subscription')
      return c.signed_in ? 'ChatGPT subscription · signed in' : 'ChatGPT subscription · not signed in'
    if (c.key_source === 'secret') return `${c.secret?.name || 'secret'} ${c.secret?.hint || ''}`.trim()
    if (c.key_source === 'shared') return `${c.shared_key?.env || 'provider key'} ${c.shared_key?.hint || ''}`.trim()
    if (c.key_source === 'not_needed') return 'no key needed'
    return 'no key — pick a secret or set ' + (c.shared_key?.env || 'the provider key')
  }
</script>

<div class="setgroup">Text</div>

{#if err}<p class="muted" style="color:#d8552f;font-size:13px">{err}</p>{/if}

{#if envOverride}
  <div class="llmenv">
    Pinned by environment{envOverride.provider ? ` · provider ${envOverride.provider}` : ''}{envOverride.model ? ` · model ${envOverride.model}` : ''}.
    These override the active configuration below until unset.
  </div>
{/if}

{#if !configs.length && !editing}
  <p class="muted" style="font-size:13px">No configurations yet — add one below to connect a model.</p>
{/if}

{#each configs as c (c.id)}
  <!-- Click the row to make it the active config (unless it already is, or the
       editor is open). Action buttons below stopPropagation so they never activate. -->
  <div
    class="llmrow" class:active={c.active} class:clickable={!c.active && !editing && !busy}
    role={!c.active && !editing ? 'button' : undefined}
    tabindex={!c.active && !editing ? 0 : undefined}
    aria-label={!c.active ? `Use ${c.name}` : undefined}
    title={!c.active && !editing ? 'Click to use this model' : ''}
    onclick={() => { if (!c.active && !busy && !editing) use(c) }}
    onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !c.active && !busy && !editing) { e.preventDefault(); use(c) } }}
  >
    <img class="llmlogo" src={LOGO[c.type]} alt="" />
    <div class="llmmeta">
      <div class="llmname">
        {c.name}
        {#if c.active}<span class="llmbadge">active</span>{/if}
      </div>
      <div class="llmsub">{TYPE_LABEL[c.type]} · {c.model}{#if endpoint(c)} · {endpoint(c)}{/if}</div>
      <div class="llmsub">
        <span class="llmkey" class:warn={c.key_source === 'none' || c.secret_missing || (c.key_source === 'subscription' && !c.signed_in)}>{keyChip(c)}</span>
        <!-- Images follow the ACTIVE config: capable types advertise the chip, and on
             the active row it reads as "image generation enabled" (✓). -->
        {#if c.images}<span class="llmkey">images{c.active ? ' ✓' : ''}</span>{/if}
        {#if tests[c.id]}
          {#if tests[c.id].testing}
            <span class="llmtest">testing…</span>
          {:else if tests[c.id].ok}
            <span class="llmtest ok">{tests[c.id].reply} · {tests[c.id].latency_ms} ms</span>
          {:else}
            <span class="llmtest bad">{tests[c.id].error}</span>
          {/if}
        {/if}
      </div>
    </div>
    <!-- While the editor is open ALL row actions are disabled — mutating or testing
         the list mid-edit invites confusion (the editor has its own Test button).
         Each button stops propagation so it never triggers the row's click-to-use. -->
    <button
      class="open" disabled={busy || tests[c.id]?.testing || !!editing}
      title={editing ? 'Editing in progress — use the editor’s Test button to test your changes' : ''}
      onclick={(e) => { e.stopPropagation(); test(c) }}
    >Test</button>
    <button class="linkbtn" disabled={busy || !!editing} onclick={(e) => { e.stopPropagation(); edit(c) }}>Edit</button>
    <button
      class="linkbtn danger" disabled={busy || !!editing}
      title={c.active ? 'Deleting the active model falls back to the next one (or defaults)' : ''}
      onclick={(e) => { e.stopPropagation(); remove(c) }}
    >Delete</button>
  </div>
{/each}

{#if editing}
  <LlmConfigForm config={editing} activate={activateOnSave} {onSaved} onCancel={() => (editing = null)} />
{:else}
  {#if !adding}
    <button class="addbtn" disabled={busy} onclick={() => (adding = true)}>
      <Icon name="plus" size={14} /> Add model
    </button>
  {:else}
    <div class="setsec">Start from a template</div>
    <div class="mcpcat">
      {#each TEMPLATES as t}
        <button class="mcpcatcard" onclick={() => pickTemplate(t)}>
          <span class="mcpcathead"><img class="llmlogo sm" src={LOGO[t.type]} alt="" /> {t.card || t.name}</span>
          <span class="mcpcatblurb">{t.blurb}</span>
        </button>
      {/each}
    </div>
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>Cancel</button>
    </div>
  {/if}
{/if}

<div class="setgroup">Live</div>
<VoiceSection />
