<script lang="ts">
  import { api } from '../transport/api/index.ts'
  import { route, go, newChatId } from '../router.ts'
  import { profileEpoch } from '../store.ts'
  import { errText } from '../lib/errors.ts'
  import type { ScreenRow } from '../schemas/screen.ts'

  let rows: ScreenRow[] = $state([])
  let error = $state('')
  $effect(() => {
    const epoch = $profileEpoch
    let stale = false
    rows = []; error = ''
    const load = async () => {
      try {
        const result = await api.screens()
        if (!stale && epoch === $profileEpoch) { rows = result.screens; error = '' }
      } catch (cause) { if (!stale) error = errText(cause) }
    }
    void load()
    const timer = setInterval(() => { void load() }, 5000)
    return () => { stale = true; clearInterval(timer) }
  })
</script>

<div class="dlist screens-nav">
  <button class="open create-screen" onclick={() => go('/c/' + newChatId())}>Create with assistant</button>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if !rows.length}<div class="none">Ask the assistant to create a dashboard, or add a Screen in Files.</div>{/if}
  {#each rows as row (row.path)}
    <button class="screen-row" class:on={$route.name === 'screen' && $route.id === row.path}
      title={row.error || row.path} onclick={() => go('/screens/s/' + encodeURIComponent(row.path))}>
      <span>{row.title}</span>
      {#if row.error}<small>Needs repair</small>{/if}
    </button>
  {/each}
</div>

<style>
  .create-screen { margin: 8px 0 16px; }
  .screen-row { display: flex; flex-direction: column; align-items: start; gap: 4px; width: 100%; padding: 12px 16px; border: 0; border-radius: 8px; background: transparent; color: var(--ink); text-align: left; }
  .screen-row:hover { background: var(--surface-hover); }
  .screen-row.on { color: var(--accent); background: var(--accent-soft); }
  small, [role=alert] { color: var(--danger); }
</style>
