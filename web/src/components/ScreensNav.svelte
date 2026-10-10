<script lang="ts">
  import { api } from '../transport/api/index.ts'
  import { route, go, newChatId } from '../router.ts'
  import { profileEpoch, screenRows } from '../store.ts'
  import { errText } from '../lib/errors.ts'
  import type { ScreenRow } from '../schemas/screen.ts'
  import Icon from './Icon.svelte'

  let error = $state('')
  let renameError = $state('')
  let menu = $state('')
  let menuPos = $state({ x: 0, y: 0 })
  let renaming = $state('')
  let renameText = $state('')
  let busy = $state(false)
  let revision = 0
  $effect(() => {
    const epoch = $profileEpoch
    let stale = false
    $screenRows = []; error = ''; renameError = ''; menu = ''; renaming = ''; busy = false
    const load = async () => {
      const version = revision
      try {
        const result = await api.screens()
        if (!stale && epoch === $profileEpoch && version === revision && !busy) {
          $screenRows = result.screens; error = ''
        }
      } catch (cause) {
        if (!stale && epoch === $profileEpoch && version === revision && !busy) error = errText(cause)
      }
    }
    void load()
    const timer = setInterval(() => { void load() }, 5000)
    return () => { stale = true; clearInterval(timer) }
  })

  function toggleMenu(event: MouseEvent & { currentTarget: HTMLElement }, row: ScreenRow) {
    event.stopPropagation()
    if (menu === row.path) { menu = ''; return }
    const rect = event.currentTarget.getBoundingClientRect()
    menuPos = { x: rect.right, y: rect.bottom + 4 }
    menu = row.path
  }
  function startRename(row: ScreenRow) {
    menu = ''; renaming = row.path; renameText = row.title; renameError = ''
  }
  async function commitRename(row: ScreenRow) {
    if (renaming !== row.path) return
    renaming = ''
    const title = renameText.trim()
    if (!title || title === row.title) return
    const epoch = $profileEpoch
    revision++; busy = true; renameError = ''
    try {
      const updated = await api.updateScreenTitle(row.path, title)
      if (epoch === $profileEpoch) $screenRows = $screenRows.map(item => item.path === row.path ? updated : item)
    } catch (cause) {
      if (epoch === $profileEpoch) renameError = errText(cause)
    } finally {
      if (epoch === $profileEpoch) { revision++; busy = false }
    }
  }
  function focusSelect(node: HTMLInputElement) { node.focus(); node.select() }
  function focusMenu(node: HTMLDivElement) { node.querySelector<HTMLButtonElement>('button')?.focus() }
  function outside(event: PointerEvent) {
    if (!(event.target instanceof Element) || !event.target.closest('.chatmenu, .rowkebab')) menu = ''
  }
</script>

<svelte:document onpointerdown={outside} onkeydown={(event) => { if (event.key === 'Escape') menu = '' }} />

<div class="dlist screens-nav" onscroll={() => menu = ''}>
  <button class="open create-screen" onclick={() => go('/c/' + newChatId())}>Create with assistant</button>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if renameError}<p role="alert">{renameError}</p>{/if}
  {#if !$screenRows.length}<div class="none">Ask the assistant to create a dashboard, or add a Screen in Files.</div>{/if}
  {#each $screenRows as row (row.path)}
    <div class="drow screen-row" class:on={$route.name === 'screen' && $route.id === row.path}
      role="button" tabindex="0" title={row.error || row.path}
      onclick={() => go('/screens/s/' + encodeURIComponent(row.path))}
      onkeydown={(event) => {
        if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault(); go('/screens/s/' + encodeURIComponent(row.path))
        }
      }}>
      {#if renaming === row.path}
        <input class="renamein" aria-label="Screen name" maxlength="200" value={renameText} use:focusSelect
          oninput={(event) => renameText = event.currentTarget.value}
          onclick={(event) => event.stopPropagation()}
          onkeydown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); void commitRename(row) }
            else if (event.key === 'Escape') renaming = ''
          }} onblur={() => void commitRename(row)} />
      {:else}
        <div class="screen-label"><span>{row.title}</span>{#if row.error}<small>Needs repair</small>{/if}</div>
        <button class="rowkebab" title="Screen actions" aria-haspopup="menu" aria-expanded={menu === row.path}
          disabled={busy} onclick={(event) => toggleMenu(event, row)}><Icon name="ellipsis-vertical" size={14} /></button>
        {#if menu === row.path}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <div class="chatmenu" role="menu" tabindex="-1" use:focusMenu
            style="left:{menuPos.x}px; top:{menuPos.y}px" onclick={(event) => event.stopPropagation()}>
            <button class="cmitem" role="menuitem" disabled={!!row.error} onclick={() => startRename(row)}>
              <Icon name="pencil" size={14} /> Rename
            </button>
          </div>
        {/if}
      {/if}
    </div>
  {/each}
</div>

<style>
  .create-screen { margin: 8px 0 16px; }
  .screen-row { display: flex; align-items: center; gap: 8px; color: var(--text); }
  .screen-label { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .screen-label span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  small, [role=alert] { color: var(--danger); }
</style>
