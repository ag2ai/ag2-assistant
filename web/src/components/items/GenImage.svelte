<script>
  // An image the agent generated/edited — shown inline as a thumbnail; click for the
  // full-size preview. The bytes live in the workspace (served via /api/files/raw).
  import { viewer, thread, taskPanel } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'

  let { item } = $props()
  const open = () =>
    ($viewer = { title: item.prompt || 'Image', name: (item.path || '').split('/').pop(), path: item.path })
  const request = $derived(requestContext($thread.items, item, $taskPanel))
</script>

<div class="genimage">
  <img class="thumb" src={api.fileUrl(item.path)} alt={item.prompt || 'generated image'}
       title={item.prompt || ''} onclick={open} />
  {#if item.path}
    <div class="itemfb"><Feedback targetKind="image" targetId={item.path} content={'Generated image — prompt: ' + (item.prompt || '')} {request} current={item.feedback} /></div>
  {/if}
</div>
