<script>
  // A file the user attached — shown so it doesn't "disappear" from the thread.
  // Images render as a thumbnail; other types as a file chip. Click → full preview
  // (the type-aware Viewer). The bytes live in the workspace (served via /api/files/raw).
  import { openAsideFile } from '../../router.js'
  import { api } from '../../transport/api.js'
  import { viewerKind } from '../../lib/preview.js'
  import Icon from '../Icon.svelte'

  let { item } = $props()
  const isImage = $derived(viewerKind(item.name || item.path) === 'image')
  const open = () => openAsideFile(item.path)
  // A thumbnail that fails to load (file moved/deleted) falls back to the chip so
  // the path stays visible instead of the browser's broken-image glyph.
  let broken = $state(false)
</script>

<div class="attach">
  <div class="who">You shared</div>
  {#if isImage && !broken}
    <img class="thumb" src={api.fileUrl(item.path)} alt={item.name} title={item.name}
         onclick={open} onerror={() => (broken = true)} />
  {:else}
    <button class="filechip" onclick={open} title={item.path}>
      <Icon name={broken ? 'image-off' : 'paperclip'} size={14} /> {broken ? item.path : (item.name || item.path)}
    </button>
  {/if}
</div>
