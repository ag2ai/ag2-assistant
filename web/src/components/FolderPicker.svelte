<script>
  // Server-driven folder browser — the gateway lists subdirectories (a browser can't hand
  // a local server a native picker's path). Navigate into folders, go up, jump to a root,
  // then "Use this folder" to confirm. `selected` (bindable) is the confirmed absolute path.
  import { onMount } from 'svelte'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'

  let { roots = {}, start = '', selected = $bindable('') } = $props()

  let current = $state('')
  let dirs = $state([])
  let parent = $state(null)
  let loading = $state(false)
  let error = $state('')

  async function load(path) {
    loading = true; error = ''
    try {
      const r = await api.listDirs(path || '')
      if (r.ok) { current = r.path; dirs = r.dirs; parent = r.parent }
      else { error = r.error || 'Could not open that folder' }
    } catch (e) { error = String(e.message || e) }
    loading = false
  }
  onMount(() => load(start || roots.cwd || roots.home || ''))
</script>

<div class="fp">
  <div class="fp-bar">
    <button class="fp-up" disabled={!parent} onclick={() => load(parent)} title="Up one folder" aria-label="Up one folder"><Icon name="chevron-left" size={15} /></button>
    <span class="fp-path" title={current}>{current || '…'}</span>
  </div>
  {#if roots.cwd || roots.home || roots.workspace}
    <div class="fp-roots">
      {#if roots.cwd}<button class="fp-root" onclick={() => load(roots.cwd)}>Launch folder</button>{/if}
      {#if roots.home}<button class="fp-root" onclick={() => load(roots.home)}>Home</button>{/if}
      {#if roots.workspace}<button class="fp-root" onclick={() => load(roots.workspace)}>Workspace</button>{/if}
    </div>
  {/if}
  <div class="fp-list">
    {#if loading}<div class="fp-msg">Loading…</div>
    {:else if error}<div class="fp-msg err">{error}</div>
    {:else if !dirs.length}<div class="fp-msg">No sub-folders here.</div>
    {:else}
      {#each dirs as d (d.path)}
        <button class="fp-dir" onclick={() => load(d.path)} title={d.path}>
          <Icon name="folder" size={15} /><span class="fp-name">{d.name}</span><Icon name="chevron-right" size={14} />
        </button>
      {/each}
    {/if}
  </div>
  <button class="fp-use" class:on={!!current && selected === current} disabled={!current} onclick={() => (selected = current)}>
    {#if !!current && selected === current}<Icon name="check" size={15} /> Selected this folder{:else}Use this folder{/if}
  </button>
</div>
