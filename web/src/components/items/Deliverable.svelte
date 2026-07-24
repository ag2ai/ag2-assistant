<script>
  import { viewer, thread, runInfo } from '../../store.js'
  import { openAsideFile } from '../../router.js'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.js'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'
  let { item } = $props()
  const request = $derived(requestContext($thread.items, item, $runInfo))
  // The preview is a flattened 240-char teaser (newlines already collapsed at the
  // source), so block markdown can't render — strip the markers for a clean one-liner.
  const teaser = (item.preview || '').replace(/[#*`>_~]/g, '').replace(/\s+/g, ' ').trim()

  // Path-less fallback: no persisted file to click, so fetch the full asset text
  // into the transient viewer store. (With a path, the filename link opens the rail.)
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

  // The deliverable's persisted workspace file — opens in the rail by file type.
  const fileName = $derived((item.path || '').split('/').pop())
  const openFile = () => openAsideFile(item.path)
</script>

<div class="deliv">
  <div class="d"><Icon name="check" size={13} /> Deliverable produced — {item.description}{#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}</div>
  {#if teaser}<div class="dprev">{teaser}…</div>{/if}
  {#if item.path}
    <div class="toolcard">
      <span class="tcicon"><Icon name="file-text" size={15} /></span>
      <button class="tclink" onclick={openFile} title={item.path}>{fileName}</button>
    </div>
  {/if}
  {#if item.taskId && !item.path}<button class="viewbtn" onclick={openFull}>View full</button>{/if}
  {#if item.deliverableId}
    <div class="itemfb"><Feedback targetKind="deliverable" targetId={item.deliverableId} content={(item.description || '') + '\n' + (item.preview || '')} {request} current={item.feedback} /></div>
  {/if}
</div>
