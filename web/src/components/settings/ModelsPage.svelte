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
  import { llmConfigs, loadLlmConfigs } from '../../lib/llm.js'
  import { TYPE_LABEL, TYPE_GROUP, TYPE_CHIP, GROUP_ORDER } from '../../lib/providerLabels.js'
  import { MODEL_TEMPLATES } from '../../lib/modelTemplates.js'
  import BrandMark from '../BrandMark.svelte'
  // One-click starting points, under a heading each. Picking a card opens the
  // editor prefilled — the two-field compatible-endpoint case is one click plus a
  // model name. The cards and their headings live in lib/ (store-free, so a test
  // can enumerate them); the page only lays them out.
  const TEMPLATE_GROUPS = GROUP_ORDER
    .map((group) => ({ group, templates: MODEL_TEMPLATES.filter((t) => TYPE_GROUP[t.type] === group) }))
    .filter((g) => g.templates.length)

  const configs = $derived($llmConfigs.configs)
  const envOverride = $derived($llmConfigs.envOverride)
  const providerDeps = $derived($llmConfigs.providerDeps || {})
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
    if (c.key_source === 'cli_login') return c.type === 'codex' ? 'Codex CLI login' : 'Claude Code CLI login'
    if (c.type === 'claude_code') return 'Claude Code adapter not found — install claude-agent-acp'
    if (c.type === 'codex') return 'Codex adapter not found — install @agentclientprotocol/codex-acp'
    if (c.key_source === 'secret') return `${c.secret?.name || 'secret'} ${c.secret?.hint || ''}`.trim()
    if (c.key_source === 'shared') return `${c.shared_key?.env || 'provider key'} ${c.shared_key?.hint || ''}`.trim()
    if (c.key_source === 'not_needed') return 'no key needed'
    return 'no key — pick a secret or set ' + (c.shared_key?.env || 'the provider key')
  }
</script>

<div class="setgroup">Text</div>

{#if err}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

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
    <BrandMark brand={c.type} />
    <div class="llmmeta">
      <div class="llmname">
        {c.name}
        {#if c.active}<span class="llmbadge">active</span>{/if}
      </div>
      <!-- An empty model is legal for the CLI-login types (= the CLI's own default);
           name it instead of leaving a dangling separator. -->
      <div class="llmsub">{TYPE_LABEL[c.type]} · {c.model || 'CLI default'}{#if endpoint(c)} · {endpoint(c)}{/if}</div>
      <div class="llmsub">
        <span class="llmkey" class:warn={c.key_source === 'none' || c.secret_missing || (c.key_source === 'subscription' && !c.signed_in)}>{keyChip(c)}</span>
        <!-- Provider library missing: name the install command, not just a dead dot. -->
        {#if c.deps && !c.deps.ok}
          <span class="llmkey warn" title={c.deps.install}>needs {c.deps.install}</span>
        {/if}
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
    {#each TEMPLATE_GROUPS as { group, templates } (group)}
      <div class="setsec">{group}</div>
      <div class="mcpcat">
        {#each templates as t}
          <button class="mcpcatcard" onclick={() => pickTemplate(t)}>
            <span class="mcpcathead">
              <BrandMark brand={t.type} size={16} /> {t.card || t.name}
              <!-- What you must bring, on every card. Marking only the exceptions
                   would make "API key" an invisible default. -->
              <span class="mcpcatchip">{TYPE_CHIP[t.type]}</span>
            </span>
            <span class="mcpcatblurb">{t.blurb}</span>
            {#if providerDeps[t.type] && !providerDeps[t.type].ok}
              <span class="mcpcatblurb warn">Needs {providerDeps[t.type].install}</span>
            {/if}
          </button>
        {/each}
      </div>
    {/each}
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>Cancel</button>
    </div>
  {/if}
{/if}

<div class="setgroup">Live</div>
<VoiceSection />
