<script>
  import { go } from '../../router.js'
  let { item } = $props()
  // The preview is a flattened 240-char teaser (newlines already collapsed at the
  // source), so block markdown can't render — strip the markers for a clean
  // one-liner and link to the task page, where the full asset renders + pops up.
  const teaser = (item.preview || '').replace(/[#*`>_~]/g, '').replace(/\s+/g, ' ').trim()
</script>

<div class="deliv">
  <div class="d">✓ Deliverable produced — {item.description}</div>
  {#if teaser}<div class="dprev">{teaser}…</div>{/if}
  {#if item.taskId}<button class="viewbtn" onclick={() => go('/t/' + item.taskId)}>View full →</button>{/if}
</div>
