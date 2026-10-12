<script lang="ts">
  import { onMount, untrack, tick } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { profileEpoch } from '../../store.ts'
  import { errText } from '../../lib/errors.ts'
  import type { CardSource, CardSourceEvent, SourceTarget } from '../../schemas/card_source.ts'
  import type { WireEvent } from '../../schemas/events.ts'
  import type { Secret } from '../../schemas/secret.ts'

  let { source, refreshState, target, onEvent, showControls = true, refreshOnLoad = false }: { source: CardSource; refreshState?: CardSourceEvent; target: SourceTarget; onEvent: (event: WireEvent) => void; showControls?: boolean; refreshOnLoad?: boolean } = $props()
  let element: HTMLDivElement
  let dialog: HTMLDialogElement
  let visible = $state(false)
  let tabVisible = $state(true)
  let busy = $state(false)
  let error = $state('')
  let version = $state('')
  let keys: Secret[] = $state([])
  let selected: Record<string, string> = $state({})
  const controlsVisible = $derived(showControls || !!error || !!refreshState?.error)
  const scheduleKey = $derived(JSON.stringify([target, source]))
  const canPoll = $derived(refreshState?.status !== 'approval_required')
  let disposed = false

  async function refresh(trigger: 'manual' | 'shown' | 'interval' = 'manual') {
    if (busy || disposed) return
    const epoch = $profileEpoch
    busy = true; error = ''
    try {
      const response = await api.refreshCardSource(target, trigger)
      if (disposed || epoch !== $profileEpoch) return
      for (const event of response.events) onEvent(event)
    } catch (cause) { if (!disposed && epoch === $profileEpoch) error = errText(cause) }
    finally { busy = false }
  }

  onMount(() => {
    if (refreshOnLoad) {
      void refresh('shown')
      return () => { disposed = true }
    }
    const observer = new IntersectionObserver(entries => { visible = entries.some(entry => entry.isIntersecting) })
    observer.observe(element.parentElement || element)
    const visibility = () => { tabVisible = document.visibilityState === 'visible' }
    visibility()
    document.addEventListener('visibilitychange', visibility)
    return () => { disposed = true; observer.disconnect(); document.removeEventListener('visibilitychange', visibility) }
  })

  $effect(() => {
    if (refreshOnLoad) return
    void scheduleKey
    const interval = untrack(() => source.interval_seconds)
    if (!visible || !tabVisible || !canPoll) return
    untrack(() => { void refresh('shown') })
    const timer = interval ? setInterval(() => { void refresh('interval') }, interval * 1000) : undefined
    return () => { if (timer) clearInterval(timer) }
  })

  async function review() {
    if (!refreshState?.code_version) { await refresh(); await tick() }
    if (!refreshState?.code_version) return
    version = refreshState.code_version
    selected = { ...source.secrets }
    dialog.showModal()
    try { keys = (await api.secrets()).secrets }
    catch (cause) { error = errText(cause) }
  }

  async function approve(approved: boolean) {
    const epoch = $profileEpoch
    busy = true; error = ''
    try {
      const bindings = Object.fromEntries(Object.entries(selected).filter(([, value]) => value))
      const response = await api.approveCardSource(target, version, approved, bindings)
      if (disposed || epoch !== $profileEpoch) return
      for (const event of response.events) onEvent(event)
      dialog.close()
      if (approved) { busy = false; await refresh() }
    } catch (cause) { error = errText(cause) }
    finally { busy = false }
  }
</script>

<div class="source-controls" class:quiet={!controlsVisible} bind:this={element}>
  {#if controlsVisible}
    <span>{source.tool === 'get_weather' ? 'Weather' : source.tool === 'get_quotes' ? 'Quotes' : 'Custom source'}</span>
    <button class="open" disabled={busy} onclick={() => refresh()}>{busy ? 'Refreshing…' : 'Refresh'}</button>
    {#if source.code}<button class="open" disabled={busy} onclick={review}>Review source</button>{/if}
    {#if error || refreshState?.error}<span role="alert">{error || refreshState?.error}</span>{/if}
  {/if}
</div>
<dialog class="source-approval" bind:this={dialog} aria-label="Approve Card source">
  <h2>Approve Card source</h2>
  <p>Allow this version to run automatically in this Profile while its Card is shown.</p>
  <pre>{source.code}</pre>
  {#each Object.entries(source.files || {}) as [name, contents]}<h3>{name}</h3><pre>{contents}</pre>{/each}
  {#each source.secret_names || [] as name}
    <label>{name}<select bind:value={selected[name]}>
      <option value="">Select a Secret</option>
      {#each keys as key}<option value={key.id}>{key.name} {key.hint}</option>{/each}
    </select></label>
  {/each}
  {#if error}<p role="alert">{error}</p>{/if}
  <div class="buttons">
    <button class="open" disabled={busy} onclick={() => approve(false)}>Decline</button>
    <button class="open" disabled={busy} onclick={() => approve(true)}>Approve version</button>
  </div>
</dialog>

<style>
  .source-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 8px 0; font-size: 12px; }
  .source-controls.quiet { display: none; }
  [role=alert] { color: var(--danger); }
  dialog { color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 16px; width: min(680px, 90vw); max-height: 85vh; overflow: auto; padding: 24px; }
  dialog::backdrop { background: #0007; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--code); padding: 12px; font-size: 12px; }
  label { display: flex; justify-content: space-between; gap: 12px; margin: 12px 0; }
  select { color: var(--ink); background: var(--surface); padding: 6px; border: 1px solid var(--line); border-radius: 8px; }
  .buttons { display: flex; justify-content: end; gap: 12px; }
</style>
