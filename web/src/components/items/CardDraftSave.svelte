<script lang="ts">
  import CardSaveMenu from './CardSaveMenu.svelte'
  import { api } from '../../transport/api/index.ts'
  import { thread } from '../../store.ts'
  import { cardFilename } from '../../lib/cardDrafts.ts'
  import type { ThreadItem } from '../../schemas/events.ts'
  import type { CardSave } from '../../schemas/card.ts'

  let { item }: { item: Extract<ThreadItem, { kind: 'a2ui' }> } = $props()
  let opened = $state(false)
  let name = $state('')
  let filename = $state('')
  let busy = $state(false)
  let error = $state('')
  let saved = $state('')
  let conflict: CardSave | null = $state(null)
  let requestId = $state('')
  let requestTarget = $state('')

  function open() {
    name = item.draft?.name || ''
    filename = cardFilename(name)
    conflict = null
    error = ''
    opened = true
  }

  function changed() {
    conflict = null
    error = ''
  }

  async function save(replace = false) {
    if (!item.draft || busy) return
    const target = JSON.stringify([name, filename, item.surfaceId])
    if (target !== requestTarget || !requestId) {
      requestTarget = target
      requestId = crypto.randomUUID()
    }
    busy = true
    error = ''
    try {
      const result = await api.saveCard($thread.chat, {
        draft_id: item.draft.id, expected_version: item.draft.version,
        surface_id: item.surfaceId, name, filename, request_id: requestId,
        replace, conflict_token: replace ? conflict?.conflict_token || '' : '',
      })
      if (result.status === 'conflict') conflict = result
      else {
        saved = result.message || `Saved to ${result.path}`
        opened = false
        requestId = ''
      }
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause)
    } finally {
      busy = false
    }
  }

  function copy() {
    name += ' copy'
    filename = cardFilename(name)
    requestId = ''
    conflict = null
  }
</script>

{#if saved}<p class="draft-save-result" role="status">{saved}</p>{/if}
<CardSaveMenu label="Save definition" icon="code" triggerClass="definition-save-trigger" alwaysVisible bind:opened onOpen={open}>
    <form onsubmit={(event) => { event.preventDefault(); save() }}>
      <h2>Save definition</h2>
      <p class="draft-version">{item.draft?.name} · Version {item.draft?.version}</p>
      <label>Card name<input bind:value={name} oninput={changed} required disabled={busy} /></label>
      <label>Filename<input bind:value={filename} oninput={changed} required disabled={busy} /></label>
      {#if conflict}<p role="alert">{conflict.message}</p>{/if}
      {#if error}<p role="alert">{error}</p>{/if}
      <div class="card-save-actions">
        <button type="button" class="open" onclick={() => opened = false} disabled={busy}>Cancel</button>
        {#if conflict}
          <button type="button" class="open" onclick={copy} disabled={busy}>Save as copy</button>
          <button type="button" class="open primary" onclick={() => save(true)} disabled={busy}>Replace</button>
        {:else}
          <button type="submit" class="open primary" disabled={busy || !name.trim() || !filename.trim()}>{busy ? 'Saving…' : 'Save definition'}</button>
        {/if}
      </div>
    </form>
</CardSaveMenu>

<style>
  .draft-save-result { margin: 8px 0 16px; font-size: 12px; }
  .draft-version { color: var(--muted); }
</style>
