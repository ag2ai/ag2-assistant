<script lang="ts">
  // Server-driven folder browser — the gateway lists subdirectories (a browser can't hand
  // a local server a native picker's path). Navigate into folders, go up, jump to a root.
  // Two commit modes: pass `onUse` for a single primary button that applies the folder you're
  // viewing in one click (Settings); otherwise `selected` (bindable) is set on confirm and the
  // host owns the commit (Onboarding's stepped flow). `busy` shows a saving state for onUse.
  import { onMount } from 'svelte'
  import { api } from '../transport/api/index.ts'
  import { invalidFolderName } from '../lib/folderName.ts'
  import { errText } from '../lib/errors.ts'
  import type { FsRoots } from '../schemas/index.ts'
  import Icon from './Icon.svelte'

  // `roots` arrives from a settings load, so a host that has not loaded yet passes
  // a partial (or nothing at all).
  type Props = {
    roots?: Partial<FsRoots>
    start?: string
    selected?: string
    onUse?: ((path: string) => void) | null
    busy?: boolean
  }

  let { roots = {}, start = '', selected = $bindable(''), onUse = null, busy = false }: Props = $props()

  let current = $state('')
  let dirs = $state<{ name: string; path: string }[]>([])
  let parent = $state<string | null>(null)
  let loading = $state(false)
  let error = $state('')

  async function load(path: string | null | undefined) {
    loading = true; error = ''
    try {
      const r = await api.listDirs(path || '')
      if (r.ok) { current = r.path; dirs = r.dirs; parent = r.parent }
      else { error = r.error || 'Could not open that folder' }
    } catch (e) { error = errText(e) }
    loading = false
  }
  onMount(() => load(start || roots.cwd || roots.home || ''))

  // ---- New sub-folder, created in the folder being viewed ----
  // Kept separate from `error`: that one REPLACES the list (a folder that wouldn't open),
  // whereas a rejected name must leave the list on screen so you can see what's already
  // there while fixing the name.
  let creating = $state(false)
  let saving = $state(false)
  let newName = $state('')
  let createErr = $state('')

  function startCreate() { creating = true; newName = ''; createErr = '' }
  function cancelCreate() { creating = false; newName = ''; createErr = '' }
  function focusRow(node: HTMLInputElement) { node.focus() }

  async function commitCreate(fromBlur = false) {
    if (!creating) return    // Enter already handled it — ignore the blur that follows unmount
    const name = newName
    const why = invalidFolderName(name)
    if (why) {
      // Clicking away with a name that can't work just backs out; pressing Enter asks for
      // a fix, because that's a deliberate attempt to create it.
      if (fromBlur) cancelCreate()
      else createErr = why
      return
    }
    creating = false
    saving = true
    try {
      const r = await api.makeDir(current, name)
      await load(r.path)     // step inside, so "Use this folder" commits the new folder
      newName = ''; createErr = ''
    } catch (e) {
      // The server owns the last word on names (it can see the filesystem) — surface its
      // message verbatim and reopen the field with the text intact so it can be edited.
      createErr = errText(e)
      creating = true
    }
    saving = false
  }
</script>

<div class="fp">
  <div class="fp-bar">
    <button class="fp-up" disabled={!parent} onclick={() => load(parent)} title="Up one folder" aria-label="Up one folder"><Icon name="chevron-left" size={15} /></button>
    <span class="fp-path" title={current}>{current || '…'}</span>
    <button class="fp-new" disabled={!current || loading || saving || creating} onclick={startCreate} title="New folder here" aria-label="New folder here"><Icon name="plus" size={15} /></button>
  </div>
  {#if roots.cwd || roots.home || roots.workspace}
    <div class="fp-roots">
      {#if roots.cwd}<button class="fp-root" onclick={() => load(roots.cwd)}>Launch folder</button>{/if}
      {#if roots.home}<button class="fp-root" onclick={() => load(roots.home)}>Home</button>{/if}
      {#if roots.workspace}<button class="fp-root" onclick={() => load(roots.workspace)}>Workspace</button>{/if}
    </div>
  {/if}
  <div class="fp-list">
    {#if creating}
      <div class="fp-newrow">
        <Icon name="folder" size={15} />
        <!-- svelte-ignore a11y_autofocus -->
        <input
          class="fp-input"
          placeholder="New folder name"
          bind:value={newName}
          use:focusRow
          onkeydown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); commitCreate() }
            else if (e.key === 'Escape') { e.preventDefault(); cancelCreate() }
            else createErr = ''
          }}
          onblur={() => commitCreate(true)}
        />
      </div>
    {/if}
    {#if saving}<div class="fp-msg">Creating…</div>
    {:else if loading}<div class="fp-msg">Loading…</div>
    {:else if error}<div class="fp-msg err">{error}</div>
    {:else if !dirs.length && !creating}<div class="fp-msg">No sub-folders here.</div>
    {:else}
      {#each dirs as d (d.path)}
        <button class="fp-dir" onclick={() => load(d.path)} title={d.path}>
          <Icon name="folder" size={15} /><span class="fp-name">{d.name}</span><Icon name="chevron-right" size={14} />
        </button>
      {/each}
    {/if}
  </div>
  {#if createErr}<div class="fp-err">{createErr}</div>{/if}
  {#if onUse}
    <button class="fp-use on" disabled={!current || busy} onclick={() => onUse(current)}>
      {#if busy}Saving…{:else}<Icon name="check" size={15} /> Use this folder{/if}
    </button>
  {:else}
    <button class="fp-use" class:on={!!current && selected === current} disabled={!current} onclick={() => (selected = current)}>
      {#if !!current && selected === current}<Icon name="check" size={15} /> Selected this folder{:else}Use this folder{/if}
    </button>
  {/if}
</div>
