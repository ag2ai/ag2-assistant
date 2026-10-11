<script lang="ts">
  // Placeholder shown while the model is still writing an A2UI payload into its
  // reply. The JSON itself is hidden (see splitA2UIText); this stands in for the
  // surface until the A2UISurface event arrives and renders the real thing.
  import Icon from '../Icon.svelte'
</script>

<div class="a2ui-surface a2ui-composing" role="status" aria-label="Composing a view">
  <div class="a2ui-composing-label">
    <Icon name="sparkles" size={15} />
    <span>Composing a view…</span>
  </div>
  <div class="a2ui-skeleton" aria-hidden="true">
    <span style="width: 62%"></span>
    <span style="width: 100%"></span>
    <span style="width: 88%"></span>
  </div>
</div>
