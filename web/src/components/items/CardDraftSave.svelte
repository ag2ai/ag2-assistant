<script lang="ts">
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

<div class="draft-save">
  <div class="draft-row">
    <span>{item.draft?.name} · Version {item.draft?.version}</span>
    <button class="open" onclick={open}>Save definition</button>
  </div>
  {#if saved}<p role="status">{saved}</p>{/if}
  {#if opened}
    <form onsubmit={(event) => { event.preventDefault(); save() }}>
      <label>Card name<input bind:value={name} oninput={changed} required disabled={busy} /></label>
      <label>Filename<input bind:value={filename} oninput={changed} required disabled={busy} /></label>
      {#if conflict}
        <p role="alert">{conflict.message}</p>
        <div class="draft-row">
          <button type="button" class="open" onclick={() => save(true)} disabled={busy}>Replace</button>
          <button type="button" class="open" onclick={copy} disabled={busy}>Save as copy</button>
        </div>
      {:else}
        <button type="submit" class="open" disabled={busy || !name.trim() || !filename.trim()}>{busy ? 'Saving…' : 'Save definition'}</button>
      {/if}
      {#if error}<p role="alert">{error}</p>{/if}
      <button type="button" class="open" onclick={() => opened = false} disabled={busy}>Cancel</button>
    </form>
  {/if}
</div>

<style>
  .draft-save { margin: 8px 0 16px; padding: 12px; border: 1px solid var(--line); border-radius: 12px; }
  .draft-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .draft-row span { font-size: 12px; color: var(--muted); }
  form { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin-top: 12px; }
  label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
  input { min-width: 180px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; color: var(--text); background: var(--bg); }
  p { width: 100%; margin: 8px 0; font-size: 12px; }
</style>
