<script lang="ts">
  // Settings → Models → Live: the install-wide list of named voice (live) configs and
  // the one active selection — the spoken counterpart of the Text list in ModelsPage,
  // built from the same row/health-map/inline-form pattern. Each config is a
  // provider + realtime model + per-config key + chosen voice; click a row to use it,
  // Test pings the provider's models list, "Change voice" opens the picker scoped to
  // that config. Backed by the shared `liveConfigs` store (lib/live.js).
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { getSettings } from './context.svelte.ts'
  import LiveConfigForm from './LiveConfigForm.svelte'
  import Icon from '../Icon.svelte'
  import { liveConfigs, loadLiveConfigs, type LiveConfigSeed } from '../../lib/live.ts'
  import { PROVIDER_LABEL } from '../../lib/providerLabels.ts'
  import BrandMark from '../BrandMark.svelte'
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type { LiveConfig, LiveProvider } from '../../schemas/index.ts'

  const ctx = getSettings()

  const configs = $derived($liveConfigs.configs)
  const providers = $derived($liveConfigs.providers)
  // One row's Test state: pending, then either the PONG reply or the error.
  type TestState = { testing?: boolean; ok?: boolean; reply?: string; latency_ms?: number; error?: string }

  let tests = $state<Record<string, TestState>>({})
  let busy = $state(false)
  let err = $state('')

  // config/template being edited in the inline form (null = closed)
  let editing = $state<LiveConfigSeed | null>(null)
  let adding = $state(false)   // provider-template grid showing
  // Two-step delete, same as the Text list above: arm the row, then Confirm.
  let confirming = $state('')

  onMount(reload)

  async function reload() {
    try { await loadLiveConfigs() } catch (e) { err = errText(e) }
  }

  async function test(c: LiveConfig) {
    tests = { ...tests, [c.id]: { testing: true } }
    try {
      tests = { ...tests, [c.id]: await api.testLiveConfig(c.id) }
    } catch (e) {
      tests = { ...tests, [c.id]: { ok: false, error: errText(e) } }
    }
  }

  async function use(c: LiveConfig) {
    err = ''; busy = true
    try { await api.useLiveConfig(c.id); await reload() } catch (e) { err = errText(e) }
    busy = false
  }

  async function remove(c: LiveConfig) {
    err = ''; busy = true
    try {
      await api.deleteLiveConfig(c.id)
      const { [c.id]: _gone, ...rest } = tests
      tests = rest
      confirming = ''
      await reload()
    } catch (e) { err = errText(e) }
    busy = false
  }

  // Open the editor: a provider template (no id → create) or an existing row (edit).
  function pickTemplate(p: LiveProvider) { adding = false; confirming = ''; editing = { provider: p.name, name: PROVIDER_LABEL[p.name] + ' Live', model: '' } }
  function edit(c: LiveConfig) { adding = false; confirming = ''; editing = { ...c } }

  const activateOnSave = $derived(editing?.id ? !!editing.active : configs.length === 0)

  async function onSaved() { editing = null; await reload() }

  function keyChip(c: LiveConfig) {
    if (c.key_source === 'secret') return `${c.secret?.name || m.llm_chip_secret_fallback()} ${c.secret?.hint || ''}`.trim()
    if (c.key_source === 'shared') return `${c.shared_key?.env || m.llm_chip_provider_key_fallback()} ${c.shared_key?.hint || ''}`.trim()
    return m.llm_chip_no_key({ env: c.shared_key?.env || m.llm_chip_provider_key_fallback() })
  }
</script>

{#if err}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

{#if !configs.length && !editing}
  <p class="muted" style="font-size:13px">{m.live_empty()}</p>
{/if}

{#each configs as c (c.id)}
  <!-- An armed row isn't click-to-use: a stray click shouldn't activate a model
       you're about to delete. -->
  {@const idle = !editing && confirming !== c.id}
  <div
    class="llmrow" class:active={c.active} class:clickable={!c.active && idle && !busy}
    role="button" aria-disabled={c.active || !idle}
    tabindex={!c.active && idle ? 0 : -1}
    aria-label={!c.active ? m.llm_use_aria({ name: c.name }) : undefined}
    title={!c.active && idle ? m.live_click_to_use() : ''}
    onclick={() => { if (!c.active && !busy && idle) use(c) }}
    onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !c.active && !busy && idle) { e.preventDefault(); use(c) } }}
  >
    <BrandMark brand={c.provider} />
    <div class="llmmeta">
      <div class="llmname">
        {c.name}
        {#if c.active}<span class="llmbadge">{m.llm_badge_active()}</span>{/if}
      </div>
      <div class="llmsub">{PROVIDER_LABEL[c.provider]} · {c.model}</div>
      <div class="llmsub">
        <span class="llmkey" class:warn={c.key_source === 'none' || c.secret_missing}>{keyChip(c)}</span>
        {#if c.voice}<span class="llmkey">{m.live_voice_chip({ voice: c.voice })}</span>{/if}
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
    {#if confirming === c.id}
      <span class="llmconfirm">{m.confirm_delete()}</span>
      <button
        class="linkbtn danger" disabled={busy}
        title={c.active ? m.live_delete_active_title() : ''}
        onclick={(e) => { e.stopPropagation(); remove(c) }}
      >{m.action_confirm()}</button>
      <button class="linkbtn" disabled={busy} onclick={(e) => { e.stopPropagation(); confirming = '' }}>{m.action_cancel()}</button>
    {:else}
      <button class="linkbtn" disabled={busy || !!editing} onclick={(e) => { e.stopPropagation(); ctx.openVoice(c.id) }}>{m.live_change_voice()}</button>
      <button
        class="open" disabled={busy || tests[c.id]?.testing || !!editing}
        onclick={(e) => { e.stopPropagation(); test(c) }}
      >{m.action_test()}</button>
      <button class="linkbtn" disabled={busy || !!editing} onclick={(e) => { e.stopPropagation(); edit(c) }}>{m.action_edit_short()}</button>
      <button
        class="linkbtn danger" disabled={busy || !!editing}
        onclick={(e) => { e.stopPropagation(); confirming = c.id }}
      >{m.action_delete()}</button>
    {/if}
  </div>
{/each}

{#if editing}
  <LiveConfigForm config={editing} {providers} activate={activateOnSave} {onSaved} onCancel={() => (editing = null)} />
{:else}
  {#if !adding}
    <button class="addbtn" disabled={busy} onclick={() => (adding = true)}>
      <Icon name="plus" size={14} /> {m.live_add()}
    </button>
  {:else}
    <div class="setsec">{m.live_choose_provider()}</div>
    <div class="mcpcat">
      {#each providers as p}
        <button class="mcpcatcard" onclick={() => pickTemplate(p)}>
          <span class="mcpcathead"><BrandMark brand={p.name} size={16} /> {PROVIDER_LABEL[p.name]}</span>
          <span class="mcpcatblurb">{m.live_realtime_blurb({ model: p.default_model })}</span>
        </button>
      {/each}
    </div>
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>{m.action_cancel()}</button>
    </div>
  {/if}
{/if}

<style>
  /* The armed-row question, sat where the row's action buttons were. */
  .llmconfirm { font-size: 12px; color: var(--danger); white-space: nowrap; }
</style>
