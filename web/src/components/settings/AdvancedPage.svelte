<script>
  // Settings → Advanced: persona memory + the AG2 architecture map and live-view toggle.
  import { getSettings } from './context.svelte.js'
  import { ag2View } from '../../store.js'
  import Icon from '../Icon.svelte'

  const ctx = getSettings()
</script>

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
  <input type="checkbox" bind:checked={$ag2View} />
  AG2 view — reveal live AG2 events + per-item provenance
</label>
