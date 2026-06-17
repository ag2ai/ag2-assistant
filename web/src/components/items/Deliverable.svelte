<script>
  import { viewer } from '../../store.js'
  import { api } from '../../transport/api.js'
  let { item } = $props()
  // The preview is a flattened 240-char teaser (newlines already collapsed at the
  // source), so block markdown can't render — strip the markers for a clean
  // one-liner. "View full" fetches the task's full asset and pops up the viewer.
  const teaser = (item.preview || '').replace(/[#*`>_~]/g, '').replace(/\s+/g, ' ').trim()

  async function openFull() {
    let text = item.preview || ''
    try {
      const t = await api.task(item.taskId)
      const ds = t.deliverables || []
      const d = ds.find((x) => x.description === item.description && x.asset)
        || ds.find((x) => x.asset)
      if (d && d.asset) text = d.asset
    } catch { /* fall back to the teaser */ }
    $viewer = { title: item.description, text }
  }
</script>

<div class="deliv">
  <div class="d">✓ Deliverable produced — {item.description}</div>
  {#if teaser}<div class="dprev">{teaser}…</div>{/if}
  {#if item.taskId}<button class="viewbtn" onclick={openFull}>View full →</button>{/if}
</div>
