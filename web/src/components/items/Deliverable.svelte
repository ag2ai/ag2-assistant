<script lang="ts">
  import { viewer, thread, runInfo } from '../../store.ts'
  import { openAsideFile } from '../../router.ts'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.ts'
  import { requestContext } from '../../lib/feedback.ts'
  import Feedback from './Feedback.svelte'
  import type { ThreadItem } from '../../schemas/events.ts'
  import { m } from '../../paraglide/messages.js'

  type Props = { item: Extract<ThreadItem, { kind: 'deliverable' }> }
  let { item }: Props = $props()
  const request = $derived(requestContext($thread.items, item, $runInfo))
  // The preview is a flattened 240-char teaser (newlines already collapsed at the
  // source), so block markdown can't render — strip the markers for a clean one-liner.
  const teaser = $derived((item.preview || '').replace(/[#*`>_~]/g, '').replace(/\s+/g, ' ').trim())

  // Path-less fallback: no persisted file to click, so show the preview in the
  // transient viewer store. (With a path, the filename link opens the rail.)
  function openFull() {
    $viewer = { title: item.description ?? '', text: item.preview ?? '' }
  }

  // The deliverable's persisted workspace file — opens in the rail by file type.
  const fileName = $derived((item.path || '').split('/').pop())
  const openFile = () => { if (item.path) openAsideFile(item.path) }
</script>

<div class="deliv">
  <div class="d"><Icon name="check" size={13} /> {m.thread_deliverable_produced()} — {item.description}{#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}</div>
  {#if teaser}<div class="dprev">{teaser}…</div>{/if}
  {#if item.path}
    <div class="toolcard">
      <span class="tcicon"><Icon name="file-text" size={15} /></span>
      <button class="tclink" onclick={openFile} title={item.path}>{fileName}</button>
    </div>
  {/if}
  {#if item.taskId && !item.path}<button class="viewbtn" onclick={openFull}>{m.thread_view_full()}</button>{/if}
  {#if item.deliverableId}
    <div class="itemfb"><Feedback targetKind="deliverable" targetId={item.deliverableId} content={(item.description || '') + '\n' + (item.preview || '')} {request} current={item.feedback} /></div>
  {/if}
</div>
