<script>
  // Settings → Models → Live: the install-wide list of named voice (live) configs and
  // the one active selection — the spoken counterpart of the Text list in ModelsPage,
  // built from the same row/health-map/inline-form pattern. Each config is a
  // provider + realtime model + per-config key + chosen voice; click a row to use it,
  // Test pings the provider's models list, "Change voice" opens the picker scoped to
  // that config. Backed by the shared `liveConfigs` store (lib/live.js).
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { getSettings } from './context.svelte.js'
  import LiveConfigForm from './LiveConfigForm.svelte'
  import Icon from '../Icon.svelte'
  import { liveConfigs, loadLiveConfigs } from '../../lib/live.js'
  import { PROVIDER_LABEL } from '../../lib/providerLabels.js'
  import BrandMark from '../BrandMark.svelte'

  const ctx = getSettings()

  const configs = $derived($liveConfigs.configs)
  const providers = $derived($liveConfigs.providers)
  let tests = $state({})       // config id -> {testing} | {ok, reply, latency_ms} | {ok:false, error}
  let busy = $state(false)
  let err = $state('')

  let editing = $state(null)   // config/template being edited in the inline form (null = closed)
  let adding = $state(false)   // provider-template grid showing

  onMount(reload)

  async function reload() {
    try { await loadLiveConfigs() } catch (e) { err = String(e.message || e) }
  }

  async function test(c) {
    tests = { ...tests, [c.id]: { testing: true } }
    try {
      tests = { ...tests, [c.id]: { ok: true, ...(await api.testLiveConfig(c.id)) } }
    } catch (e) {
      tests = { ...tests, [c.id]: { ok: false, error: String(e.message || e) } }
    }
  }

  async function use(c) {
    err = ''; busy = true
    try { await api.useLiveConfig(c.id); await reload() } catch (e) { err = String(e.message || e) }
    busy = false
  }

  async function remove(c) {
    err = ''; busy = true
    try {
      await api.deleteLiveConfig(c.id)
      const { [c.id]: _gone, ...rest } = tests
      tests = rest
      await reload()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // Open the editor: a provider template (no id → create) or an existing row (edit).
  function pickTemplate(p) { adding = false; editing = { provider: p.name, name: PROVIDER_LABEL[p.name] + ' Live', model: '' } }
  function edit(c) { adding = false; editing = { ...c } }

  const activateOnSave = $derived(editing?.id ? !!editing.active : configs.length === 0)

  async function onSaved() { editing = null; await reload() }

  function keyChip(c) {
    if (c.key_source === 'secret') return `${c.secret?.name || 'secret'} ${c.secret?.hint || ''}`.trim()
    if (c.key_source === 'shared') return `${c.shared_key?.env || 'provider key'} ${c.shared_key?.hint || ''}`.trim()
    return 'no key — pick a secret or set ' + (c.shared_key?.env || 'the provider key')
  }
</script>

{#if err}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

{#if !configs.length && !editing}
  <p class="muted" style="font-size:13px">No live models yet — add one below to talk to the assistant.</p>
{/if}

{#each configs as c (c.id)}
  <div
    class="llmrow" class:active={c.active} class:clickable={!c.active && !editing && !busy}
    role={!c.active && !editing ? 'button' : undefined}
    tabindex={!c.active && !editing ? 0 : undefined}
    aria-label={!c.active ? `Use ${c.name}` : undefined}
    title={!c.active && !editing ? 'Click to use this live model' : ''}
    onclick={() => { if (!c.active && !busy && !editing) use(c) }}
    onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !c.active && !busy && !editing) { e.preventDefault(); use(c) } }}
  >
    <BrandMark brand={c.provider} />
    <div class="llmmeta">
      <div class="llmname">
        {c.name}
        {#if c.active}<span class="llmbadge">active</span>{/if}
      </div>
      <div class="llmsub">{PROVIDER_LABEL[c.provider]} · {c.model}</div>
      <div class="llmsub">
        <span class="llmkey" class:warn={c.key_source === 'none' || c.secret_missing}>{keyChip(c)}</span>
        {#if c.voice}<span class="llmkey">voice: {c.voice}</span>{/if}
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
    <button class="linkbtn" disabled={busy || !!editing} onclick={(e) => { e.stopPropagation(); ctx.openVoice(c.id) }}>Change voice</button>
    <button
      class="open" disabled={busy || tests[c.id]?.testing || !!editing}
      onclick={(e) => { e.stopPropagation(); test(c) }}
    >Test</button>
    <button class="linkbtn" disabled={busy || !!editing} onclick={(e) => { e.stopPropagation(); edit(c) }}>Edit</button>
    <button
      class="linkbtn danger" disabled={busy || !!editing}
      title={c.active ? 'Deleting the active live model falls back to the next one (or legacy)' : ''}
      onclick={(e) => { e.stopPropagation(); remove(c) }}
    >Delete</button>
  </div>
{/each}

{#if editing}
  <LiveConfigForm config={editing} {providers} activate={activateOnSave} {onSaved} onCancel={() => (editing = null)} />
{:else}
  {#if !adding}
    <button class="addbtn" disabled={busy} onclick={() => (adding = true)}>
      <Icon name="plus" size={14} /> Add live model
    </button>
  {:else}
    <div class="setsec">Choose a provider</div>
    <div class="mcpcat">
      {#each providers as p}
        <button class="mcpcatcard" onclick={() => pickTemplate(p)}>
          <span class="mcpcathead"><BrandMark brand={p.name} size={16} /> {PROVIDER_LABEL[p.name]}</span>
          <span class="mcpcatblurb">Realtime voice · {p.default_model}</span>
        </button>
      {/each}
    </div>
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>Cancel</button>
    </div>
  {/if}
{/if}
