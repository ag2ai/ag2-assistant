<script>
  // Profile editor → Memory tab (ADR 0015, redesign §8): this profile's persona memory —
  // preferences and context the assistant has learned for THIS persona only (the shared
  // "Who you are" identity lives in Settings → Advanced). The doc is free-form markdown
  // (getMemory/setMemory), so instead of a half-screen textarea we show a compact state:
  // an empty-state card when nothing's learned, else a clamped preview card. Editing is an
  // explicit, opt-in expansion — the raw textarea only appears when you choose to Edit.
  // Re-points to the active profile's persona on each profile switch (profileEpoch).
  import { profileEpoch } from '../../store.js'
  import { api } from '../../transport/api/index.ts'

  let text = $state('')
  let draft = $state('')
  let loading = $state(true)
  let editing = $state(false)
  let busy = $state(false)
  let saved = $state(false)
  let err = $state('')

  const hasMemory = $derived(!!text.trim())

  async function load() {
    loading = true; err = ''; editing = false
    try { text = (await api.getMemory().then((r) => r.text)) || '' } catch (e) { err = String(e.message || e) }
    loading = false
  }
  // Re-load when the active profile changes (persona re-points on switch).
  $effect(() => { $profileEpoch; load() })

  function startEdit() { draft = text; editing = true; err = '' }
  function cancelEdit() { editing = false; err = '' }

  async function save() {
    err = ''; busy = true
    try {
      await api.setMemory(draft)
      text = draft
      editing = false
      saved = true
      setTimeout(() => (saved = false), 1500)
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  async function clearAll() {
    err = ''; busy = true
    try {
      await api.setMemory('')
      text = ''
      editing = false
    } catch (e) { err = String(e.message || e) }
    busy = false
  }
</script>

<p class="mhint">What this profile has learned about you.</p>
{#if err}<p class="merr">{err}</p>{/if}

{#if loading}
  <p class="muted" style="font-size:13px;margin:0">Loading…</p>
{:else if editing}
  <textarea class="marea" bind:value={draft} spellcheck="false" placeholder="Preferences and context for this persona only…"></textarea>
  <div class="mfoot">
    <button class="linkbtn" disabled={busy} onclick={cancelEdit}>Cancel</button>
    <button class="open" disabled={busy} onclick={save}>{busy ? 'Saving…' : 'Save'}</button>
  </div>
{:else if !hasMemory}
  <div class="mempty">
    <div class="memptytitle">No memories yet</div>
    <p class="memptybody">The assistant will gradually learn preferences and context specific to this profile as you chat. You can also add notes yourself.</p>
    <button class="linkbtn" onclick={startEdit}>Add a note</button>
  </div>
{:else}
  <div class="mcard">
    <pre class="mpreview">{text}</pre>
  </div>
  <div class="mfoot">
    {#if saved}<span class="okmsg">Saved ✓</span>{/if}
    <button class="linkbtn quiet" disabled={busy} onclick={clearAll}>Clear memory</button>
    <button class="open" onclick={startEdit}>Edit</button>
  </div>
{/if}

<style>
  .mhint { font-size: var(--text-xs); color: var(--text-muted); margin: 0 0 10px; }
  .merr { color: var(--danger, var(--danger)); font-size: var(--text-sm); margin: 0 0 8px; }

  .mempty {
    display: flex; flex-direction: column; gap: 6px; align-items: flex-start;
    padding: 22px; text-align: left;
    background: var(--surface-sunk); border: 1px dashed var(--line); border-radius: var(--radius-md, 12px);
  }
  .memptytitle { font-size: var(--text-sm); font-weight: var(--fw-semibold); }
  .memptybody { font-size: var(--text-xs); color: var(--text-muted); line-height: var(--leading-snug); margin: 0 0 4px; max-width: 44ch; }

  .mcard {
    max-height: 260px; overflow: auto;
    padding: 14px; background: var(--surface-sunk); border: 1px solid var(--line); border-radius: var(--radius-md, 12px);
  }
  .mpreview {
    margin: 0; font: inherit; font-size: var(--text-sm); line-height: var(--leading-normal, 1.5);
    color: var(--text); white-space: pre-wrap; word-break: break-word;
  }

  .marea {
    width: 100%; min-height: 220px; resize: vertical; box-sizing: border-box;
    border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 10px 12px;
    font: inherit; font-size: var(--text-sm); line-height: 1.5; background: var(--bg); color: var(--text);
  }
  .marea:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }

  .mfoot { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 10px; }
  .okmsg { color: var(--accent); font-size: var(--text-xs); }
  .linkbtn.quiet { color: var(--text-muted); }
  .linkbtn.quiet:hover { color: var(--danger, var(--danger)); }
</style>
