<script>
  // An image the agent generated/edited — shown inline as a thumbnail; click for the
  // full-size preview. The bytes live in the workspace (served via /api/files/raw).
  import { thread, taskPanel } from '../../store.js'
  import { openAsideFile } from '../../router.js'
  import { api } from '../../transport/api.js'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'
  import Icon from '../Icon.svelte'

  let { item } = $props()
  const open = () => openAsideFile(item.path)
  const request = $derived(requestContext($thread.items, item, $taskPanel))
  // A thumbnail that fails to load (file moved/deleted) falls back to a chip so
  // the path stays visible instead of the browser's broken-image glyph.
  let broken = $state(false)
</script>

<div class="genimage">
  {#if broken}
    <button class="filechip" onclick={open} title={item.path}>
      <Icon name="image-off" size={14} /> {item.path}
    </button>
  {:else}
    <img class="thumb" src={api.fileUrl(item.path)} alt={item.prompt || 'generated image'}
         title={item.prompt || ''} onclick={open} onerror={() => (broken = true)} />
  {/if}
  {#if item.path}
    <div class="itemfb"><Feedback targetKind="image" targetId={item.path} content={'Generated image — prompt: ' + (item.prompt || '')} {request} current={item.feedback} /></div>
  {/if}
</div>
