<script lang="ts">
  // Placeholder shown while the model is still writing an A2UI payload into its
  // reply. The JSON itself is hidden (see splitA2UIText); this stands in for the
  // surface until the A2UISurface event arrives and renders the real thing.
  import Icon from '../Icon.svelte'
</script>

<div class="a2ui a2ui-composing" role="status" aria-label="Composing a view">
  <div class="a2ui-head">
    <span class="a2ui-mark"><Icon name="sparkles" size={15} /></span>
    <span class="a2ui-headtext">
      <span class="a2ui-eyebrow">A2UI</span>
      <span class="a2ui-title">Composing a view…</span>
    </span>
  </div>
  <div class="a2ui-skeleton">
    <span style="width: 62%"></span>
    <span style="width: 100%"></span>
    <span style="width: 88%"></span>
  </div>
</div>
