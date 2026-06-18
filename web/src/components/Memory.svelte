<script>
  import { onMount } from 'svelte'
  import { memoryOpen } from '../store.js'
  import { api } from '../transport/api.js'

  let text = $state('')
  let loading = $state(true)
  let saved = $state(false)
  let err = $state('')

  async function load() {
    try { text = (await api.getMemory()).text || '' } catch (e) { err = String(e.message || e) }
    loading = false
  }
  onMount(load)

  async function save() {
    err = ''
    try { await api.setMemory(text); saved = true; setTimeout(() => (saved = false), 1500) }
    catch (e) { err = String(e.message || e) }
  }
  const close = () => ($memoryOpen = false)
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal memory">
  <h2>Memory</h2>
  <p class="muted">What the assistant has learned about you. Edit freely — your changes become the base it keeps building on. (The assistant also keeps refining this as you chat.)</p>
  {#if err}<p class="muted" style="color:#d8552f">{err}</p>{/if}
  {#if loading}
    <p class="muted">Loading…</p>
  {:else}
    <textarea bind:value={text} spellcheck="false" placeholder="(nothing learned yet)"></textarea>
  {/if}
  <div class="mfoot">
    {#if saved}<span class="okmsg">Saved ✓</span>{/if}
    <button class="open" onclick={save} disabled={loading}>Save</button>
    <button class="modal-close" onclick={close}>Close</button>
  </div>
</div>
