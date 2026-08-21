<script lang="ts">
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
  import { api } from '../../transport/api/index.ts'
  import LlmConfigForm from './LlmConfigForm.svelte'
  import VoiceSection from './VoiceSection.svelte'
  import Icon from '../Icon.svelte'
  import { llmConfigs, loadLlmConfigs, type LlmConfigSeed } from '../../lib/llm.ts'
  import { builtinToolText, builtinChip } from '../../lib/builtinTools.ts'
  import { typeLabel, TYPE_GROUP, TYPE_CHIP, GROUP_ORDER, SUBSCRIPTION_GROUP } from '../../lib/providerLabels.ts'
  import { MODEL_TEMPLATES, templateCard, type ModelTemplate } from '../../lib/modelTemplates.ts'
  import BrandMark from '../BrandMark.svelte'
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type { LlmConfig } from '../../schemas/index.ts'

  // The template cards, bucketed under their headings in render order — picking
  // one opens the editor prefilled. Empty groups drop out rather than head nothing.
  const TEMPLATE_GROUPS = GROUP_ORDER
    .map((group) => ({ group, templates: MODEL_TEMPLATES.filter((t) => TYPE_GROUP[t.type] === group) }))
    .filter((g) => g.templates.length)

  // Display mapping only: the GROUP_ORDER/TYPE_GROUP values stay the matching keys
  // (they are compared by string equality against SUBSCRIPTION_GROUP and each other),
  // so localization happens here at render time. Vendor names pass through untouched.
  const groupLabel = (group: string) => (group === SUBSCRIPTION_GROUP ? m.llm_group_subscription() : group)
  // TYPE_CHIP values 'OAuth'/'ACP' are protocol names (and matched by equality in
  // tests); only the plain-English chips read localized.
  const chipLabel = (chip: string) =>
    chip === 'API key' ? m.llm_chip_api_key() : chip === 'no key' ? m.llm_chip_no_key_short() : chip

  const configs = $derived($llmConfigs.configs)
  const envOverride = $derived($llmConfigs.envOverride)
  const providerDeps = $derived($llmConfigs.providerDeps || {})
  // One row's Test state: pending, then either the PONG reply or the error. The
  // three shapes share one open record so the row can read any field it needs.
  type TestState = { testing?: boolean; ok?: boolean; reply?: string; latency_ms?: number; error?: string }

  let tests = $state<Record<string, TestState>>({})
  let busy = $state(false)
  let err = $state('')

  // config/template being edited in the inline form (null = closed)
  let editing = $state<LlmConfigSeed | null>(null)
  let adding = $state(false)   // template card grid showing
  // Two-step delete (the SkillsPage idiom): first click arms the row by id, the
  // Confirm next to it actually deletes. Deleting a model is unrecoverable, and
  // Delete sits one button away from Edit.
  let confirming = $state('')

  onMount(reload)

  // Thin wrapper: refresh the shared store and surface any failure in this page's err.
  async function reload() {
    try { await loadLlmConfigs() } catch (e) { err = errText(e) }
  }

  // Test = per-row health map, exactly like McpServers.check.
  async function test(c: LlmConfig) {
    tests = { ...tests, [c.id]: { testing: true } }
    try {
      tests = { ...tests, [c.id]: await api.testLlmConfig(c.id) }
    } catch (e) {
      tests = { ...tests, [c.id]: { ok: false, error: errText(e) } }
    }
  }

  async function use(c: LlmConfig) {
    err = ''; busy = true
    try { await api.useLlmConfig(c.id); await reload() } catch (e) { err = errText(e) }
    busy = false
  }

  async function remove(c: LlmConfig) {
    err = ''; busy = true
    try {
      await api.deleteLlmConfig(c.id)
      const { [c.id]: _gone, ...rest } = tests
      tests = rest
      confirming = ''
      await reload()
    } catch (e) { err = errText(e) }
    busy = false
  }

  // Open the editor: a template (no id → create) or an existing row (edit).
  function pickTemplate(t: ModelTemplate) { adding = false; confirming = ''; editing = { ...t } }
  function edit(c: LlmConfig) { adding = false; confirming = ''; editing = { ...c } }

  // The save should activate when it's the first-ever config (empty store), or when
  // re-saving the already-active config (keep it active). Otherwise the explicit Use
  // button owns activation.
  const activateOnSave = $derived(editing?.id ? !!editing.active : configs.length === 0)

  async function onSaved() { editing = null; await reload() }

  const endpoint = (c: LlmConfig) => (c.type === 'ollama' ? c.host : c.base_url) || ''

  // Honest key chip: name the key an actual call would send (key_source from the
  // server), not just whether a per-config key exists — a keyless Gemini config
  // still works via the shared env key, and a base_url config needs none at all.
  // Hints already carry the "…" ellipsis (e.g. "…abcd").
  function keyChip(c: LlmConfig) {
    if (c.key_source === 'subscription')
      return c.signed_in ? m.llm_chip_sub_signed_in() : m.llm_chip_sub_not_signed_in()
    if (c.key_source === 'cli_login') return c.type === 'codex' ? m.llm_chip_codex_cli() : m.llm_chip_claude_cli()
    if (c.type === 'claude_code') return m.llm_chip_claude_adapter_missing()
    if (c.type === 'codex') return m.llm_chip_codex_adapter_missing()
    if (c.key_source === 'secret') return `${c.secret?.name || m.llm_chip_secret_fallback()} ${c.secret?.hint || ''}`.trim()
    if (c.key_source === 'shared') return `${c.shared_key?.env || m.llm_chip_provider_key_fallback()} ${c.shared_key?.hint || ''}`.trim()
    if (c.key_source === 'not_needed') return m.llm_chip_no_key_needed()
    return m.llm_chip_no_key({ env: c.shared_key?.env || m.llm_chip_provider_key_fallback() })
  }
</script>

<!-- One editor, two homes: under the row being edited (Edit on an existing config),
     or at the foot of the list (a template picked from Add model). -->
{#snippet editorForm()}
  <!-- Both call sites already stand inside an `editing` check; this one is what says
       so to the type checker, which a snippet body cannot be narrowed through. -->
  {#if editing}
    <LlmConfigForm config={editing} activate={activateOnSave} {onSaved} onCancel={() => (editing = null)} />
  {/if}
{/snippet}

<div class="setgroup">{m.llm_group_text()}</div>

{#if err}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

{#if envOverride}
  <div class="llmenv">
    {m.llm_env_pinned()}{envOverride.provider ? ` · ${m.llm_env_provider({ name: envOverride.provider })}` : ''}{envOverride.model ? ` · ${m.llm_env_model({ name: envOverride.model })}` : ''}.
    {m.llm_env_note()}
  </div>
{/if}

{#if !configs.length && !editing}
  <p class="muted" style="font-size:13px">{m.llm_empty()}</p>
{/if}

{#each configs as c (c.id)}
  <!-- Click the row to make it the active config (unless it already is, the editor is
       open, or the row is armed for delete — a stray click there shouldn't activate a
       model you're about to remove). Action buttons stopPropagation so they never
       activate either. -->
  {@const idle = !editing && confirming !== c.id}
  <div
    class="llmrow" class:active={c.active} class:clickable={!c.active && idle && !busy}
    role="button" aria-disabled={c.active || !idle}
    tabindex={!c.active && idle ? 0 : -1}
    aria-label={!c.active ? m.llm_use_aria({ name: c.name }) : undefined}
    title={!c.active && idle ? m.llm_click_to_use() : ''}
    onclick={() => { if (!c.active && !busy && idle) use(c) }}
    onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !c.active && !busy && idle) { e.preventDefault(); use(c) } }}
  >
    <BrandMark brand={c.type} />
    <div class="llmmeta">
      <div class="llmname">
        {c.name}
        {#if c.active}<span class="llmbadge">{m.llm_badge_active()}</span>{/if}
      </div>
      <!-- An empty model is legal for the CLI-login types (= the CLI's own default);
           name it instead of leaving a dangling separator. -->
      <div class="llmsub">{typeLabel(c.type)} · {c.model || m.llm_cli_default()}{#if endpoint(c)} · {endpoint(c)}{/if}</div>
      <div class="llmsub">
        <span class="llmkey" class:warn={c.key_source === 'none' || c.secret_missing || (c.key_source === 'subscription' && !c.signed_in)}>{keyChip(c)}</span>
        <!-- Provider library missing: name the install command, not just a dead dot. -->
        {#if c.deps && !c.deps.ok}
          <span class="llmkey warn" title={c.deps.install}>{m.llm_needs_install({ install: c.deps.install })}</span>
        {/if}
        <!-- Images follow the ACTIVE config: capable types advertise the chip, and on
             the active row it reads as "image generation enabled" (✓). -->
        {#if c.images}<span class="llmkey">{m.llm_chip_images()}{c.active ? ' ✓' : ''}</span>{/if}
        <!-- Provider-native tools switched on, so the list says what a config can do
             without opening it. Short chips: the full labels are the form's. -->
        {#each Object.keys(c.builtin_tools || {}) as id (id)}
          <span class="llmkey" title={builtinToolText(c.type, id).label}>{builtinChip(id)}</span>
        {/each}
        {#if tests[c.id]}
          {#if tests[c.id].testing}
            <span class="llmtest">{m.llm_testing()}</span>
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
    {#if confirming === c.id}
      <span class="llmconfirm">{m.confirm_delete()}</span>
      <button
        class="linkbtn danger" disabled={busy}
        title={c.active ? m.llm_delete_active_title() : ''}
        onclick={(e) => { e.stopPropagation(); remove(c) }}
      >{m.action_confirm()}</button>
      <button class="linkbtn" disabled={busy} onclick={(e) => { e.stopPropagation(); confirming = '' }}>{m.action_cancel()}</button>
    {:else}
      <button
        class="open" disabled={busy || tests[c.id]?.testing || !!editing}
        title={editing ? m.llm_editing_title() : ''}
        onclick={(e) => { e.stopPropagation(); test(c) }}
      >{m.action_test()}</button>
      <button class="linkbtn" disabled={busy || !!editing} onclick={(e) => { e.stopPropagation(); edit(c) }}>{m.action_edit_short()}</button>
      <button
        class="linkbtn danger" disabled={busy || !!editing}
        title={c.active ? m.llm_delete_active_title() : ''}
        onclick={(e) => { e.stopPropagation(); confirming = c.id }}
      >{m.action_delete()}</button>
    {/if}
  </div>
  {#if editing?.id === c.id}{@render editorForm()}{/if}
{/each}

<!-- An existing config's editor already rendered under its own row above, so down
     here only a freshly picked template still needs a home. -->
{#if editing && !editing.id}
  {@render editorForm()}
{:else if !editing}
  {#if !adding}
    <button class="addbtn" disabled={busy} onclick={() => (adding = true)}>
      <Icon name="plus" size={14} /> {m.llm_add_model()}
    </button>
  {:else}
    {#each TEMPLATE_GROUPS as { group, templates } (group)}
      <div class="setsec">{groupLabel(group)}</div>
      <div class="mcpcat">
        {#each templates as t}
          <button class="mcpcatcard" onclick={() => pickTemplate(t)}>
            <span class="mcpcathead">
              <BrandMark brand={t.type} size={16} /> {templateCard(t)}
              <!-- What this card asks you to bring, worn by every card. -->
              <span class="mcpcatchip">{chipLabel(TYPE_CHIP[t.type])}</span>
            </span>
            <span class="mcpcatblurb">{t.blurb()}</span>
            {#if providerDeps[t.type] && !providerDeps[t.type].ok}
              <span class="mcpcatblurb warn">{m.llm_needs_install_cap({ install: providerDeps[t.type].install })}</span>
            {/if}
          </button>
        {/each}
      </div>
    {/each}
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>{m.action_cancel()}</button>
    </div>
  {/if}
{/if}

<div class="setgroup">{m.llm_group_live()}</div>
<VoiceSection />

<style>
  /* The armed-row question, sat where the Test/Edit/Delete buttons were. */
  .llmconfirm { font-size: 12px; color: var(--danger); white-space: nowrap; }
</style>
