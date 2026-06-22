<script>
  // A file the user attached — shown so it doesn't "disappear" from the thread.
  // Images render as a thumbnail; other types as a file chip. Click → full preview
  // (the type-aware Viewer). The bytes live in the workspace (served via /api/files/raw).
  import { viewer } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { viewerKind } from '../../lib/preview.js'
  import Icon from '../Icon.svelte'

  let { item } = $props()
  const isImage = $derived(viewerKind(item.name || item.path) === 'image')
  const open = () =>
    ($viewer = { title: item.name || 'Attachment', name: item.name || (item.path || '').split('/').pop(), path: item.path })
</script>

<div class="attach">
  <div class="who">You shared</div>
  {#if isImage}
    <img class="thumb" src={api.fileUrl(item.path)} alt={item.name} title={item.name} onclick={open} />
  {:else}
    <button class="filechip" onclick={open} title={item.path}><Icon name="paperclip" size={14} /> {item.name || item.path}</button>
  {/if}
</div>
