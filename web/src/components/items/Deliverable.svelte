<script>
  let { item } = $props()
</script>

<div class="deliv">
  <div class="d">✓ Deliverable produced — {item.description}</div>
  {#if item.preview}<div>{item.preview}…</div>{/if}
</div>
