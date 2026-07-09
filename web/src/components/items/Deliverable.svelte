<script>
  import { viewer, thread, taskPanel } from '../../store.js'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.js'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'
  let { item } = $props()
  const request = $derived(requestContext($thread.items, item, $taskPanel))
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

  // The deliverable was also persisted as a workspace file — link it (open in the
  // viewer by file type; the viewer header offers the download).
  const fileName = $derived((item.path || '').split('/').pop())
  const openFile = () => ($viewer = { title: item.description, name: fileName, path: item.path })
</script>

<div class="deliv">
  <div class="d"><Icon name="check" size={13} /> Deliverable produced — {item.description}{#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}</div>
  {#if teaser}<div class="dprev">{teaser}…</div>{/if}
  {#if item.path}
    <div class="toolcard">
      <span class="tcicon"><Icon name="file-text" size={15} /></span>
      <button class="tclink" onclick={openFile} title={item.path}>{fileName}</button>
      <a class="tcdl" href={api.fileUrl(item.path, true)} title="Download {fileName}" aria-label="Download {fileName}"><Icon name="download" size={15} /></a>
    </div>
  {/if}
  {#if item.taskId}<button class="viewbtn" onclick={openFull}>View full</button>{/if}
  {#if item.deliverableId}
    <div class="itemfb"><Feedback targetKind="deliverable" targetId={item.deliverableId} content={(item.description || '') + '\n' + (item.preview || '')} {request} current={item.feedback} /></div>
  {/if}
</div>
