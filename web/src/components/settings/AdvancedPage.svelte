<script>
  // Settings → Advanced: persona memory + the AG2 architecture map and live-view toggle.
  import { getSettings } from './context.svelte.js'
  import { ag2View } from '../../store.js'
  import { toggleAsideInspector } from '../../router.js'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'

  const ctx = getSettings()
  let replyTimeout = $state('')

  $effect(() => {
    if (ctx.s) replyTimeout = String(ctx.s.reply_timeout_s)
  })

  function saveReplyTimeout() {
    const seconds = Number(replyTimeout)
    if (!Number.isFinite(seconds) || seconds <= 0) {
      ctx.err = 'Chat turn timeout must be greater than zero.'
      return
    }
    ctx.run(() => api.setReplyTimeout(seconds))
  }
</script>

<div class="setsec">Turn budget</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="clock" size={15} /> Chat turn timeout</span>
    <span class="sv">Total time for model calls, tools, and questions</span>
  </div>
  <label class="timeoutfield">
    <input type="number" min="1" max="3600" step="1" bind:value={replyTimeout} aria-label="Chat turn timeout in seconds" />
    <span>sec</span>
  </label>
  <button class="open" disabled={ctx.busy} onclick={saveReplyTimeout}>Save</button>
</div>

<div class="setsec">Memory</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="brain" size={15} /> Memory</span>
    <span class="sv">what the assistant has learned about you</span>
  </div>
  <button class="open" onclick={ctx.openMemory}>View & edit</button>
</div>

<div class="setsec">AG2</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="zap" size={15} /> Powered by AG2</span>
    <span class="sv">the AG2 primitives behind this app</span>
  </div>
  <button class="open" onclick={ctx.openPoweredBy}>View</button>
</div>
<label class="setcheck">
  <input type="checkbox" checked={$ag2View} onchange={toggleAsideInspector} />
  AG2 view — reveal live AG2 events + per-item provenance
</label>

<style>
  .timeoutfield { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 13px; }
  .timeoutfield input { width: 66px; border: 1px solid var(--line); border-radius: 8px; padding: 7px 8px; font: inherit; background: var(--bg); color: var(--ink); }
  .timeoutfield input:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
</style>
