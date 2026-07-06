<script>
  import { onMount } from 'svelte'
  import { memoryOpen, profiles } from '../store.js'
  import { api } from '../transport/api.js'

  // Universal "who the user is" — one doc shared by every profile (identity facts).
  let uniText = $state('')
  // This profile's persona memory (learned + curated for the active persona).
  let proText = $state('')
  let loading = $state(true)
  let saved = $state(false)
  let err = $state('')

  const activeName = $derived(
    ($profiles.list.find((p) => p.id === $profiles.activeId) || {}).name || 'this profile',
  )

  async function load() {
    try {
      const [u, p] = await Promise.all([api.globalMemory(), api.getMemory()])
      uniText = u.text || ''
      proText = p.text || ''
    } catch (e) {
      err = String(e.message || e)
    }
    loading = false
  }
  onMount(load)

  async function save() {
    err = ''
    try {
      // Save both layers; the universal one is shared, the persona one is per-profile.
      await Promise.all([api.setGlobalMemory(uniText), api.setMemory(proText)])
      saved = true
      setTimeout(() => (saved = false), 1500)
    } catch (e) {
      err = String(e.message || e)
    }
  }
  const close = () => ($memoryOpen = false)
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal memory">
  <h2>Memory</h2>
  <p class="muted">What the assistant knows about you. Edit freely — your changes become the base it keeps building on.</p>
  {#if err}<p class="muted" style="color:#d8552f">{err}</p>{/if}
  {#if loading}
    <p class="muted">Loading…</p>
  {:else}
    <div class="msection">
      <h3>Who you are — shared across profiles</h3>
      <p class="muted small">Identity facts (name, location, timezone, family, writing voice) that are true no matter which profile you're in. Every profile sees this.</p>
      <textarea bind:value={uniText} spellcheck="false" placeholder="(nothing here yet)"></textarea>
    </div>
    <div class="msection">
      <h3>This profile's memory — {activeName}</h3>
      <p class="muted small">Preferences and context for this persona only. The assistant also keeps refining this as you chat.</p>
      <textarea bind:value={proText} spellcheck="false" placeholder="(nothing learned yet)"></textarea>
    </div>
  {/if}
  <div class="mfoot">
    {#if saved}<span class="okmsg">Saved ✓</span>{/if}
    <button class="open" onclick={save} disabled={loading}>Save</button>
    <button class="modal-close" onclick={close}>Close</button>
  </div>
</div>
