<script>
  // An image the agent generated/edited — shown inline as a thumbnail; click for the
  // full-size preview. The bytes live in the workspace (served via /api/files/raw).
  import { thread, runInfo } from '../../store.ts'
  import { openAsideFile } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'
  import Icon from '../Icon.svelte'

  let { item } = $props()
  const open = () => openAsideFile(item.path)
  const request = $derived(requestContext($thread.items, item, $runInfo))
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
    <!-- The thumbnail opens the file, so the click target is a button; the img is
         decoration inside it and keeps no listener of its own. -->
    <button class="thumbbtn" onclick={open} title={item.prompt || ''} aria-label="Open generated image">
      <img class="thumb" src={api.fileUrl(item.path)} alt={item.prompt || 'generated image'}
           onerror={() => (broken = true)} />
    </button>
  {/if}
  {#if item.path}
    <div class="itemfb"><Feedback targetKind="image" targetId={item.path} content={'Generated image — prompt: ' + (item.prompt || '')} {request} current={item.feedback} /></div>
  {/if}
</div>
