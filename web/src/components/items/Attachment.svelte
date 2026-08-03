<script lang="ts">
  // A file the user attached — shown so it doesn't "disappear" from the thread.
  // Images render as a thumbnail; other types as a file chip. Click → full preview
  // (the type-aware Viewer). The bytes live in the workspace (served via /api/files/raw).
  import { openAsideFile } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import { viewerKind } from '../../lib/preview.ts'
  import Icon from '../Icon.svelte'
  import type { ThreadItem } from '../../schemas/events.ts'

  type Props = { item: Extract<ThreadItem, { kind: 'attachment' }> }
  let { item }: Props = $props()
  const isImage = $derived(viewerKind(item.name || item.path) === 'image')
  const open = () => openAsideFile(item.path)
  // A thumbnail that fails to load (file moved/deleted) falls back to the chip so
  // the path stays visible instead of the browser's broken-image glyph.
  let broken = $state(false)
</script>

<div class="attach">
  <div class="who">You shared</div>
  {#if isImage && !broken}
    <!-- The thumbnail opens the file, so the click target is a button; the img is
         decoration inside it and keeps no listener of its own. -->
    <button class="thumbbtn" onclick={open} title={item.name} aria-label="Open {item.name || item.path}">
      <img class="thumb" src={api.fileUrl(item.path)} alt={item.name} onerror={() => (broken = true)} />
    </button>
  {:else}
    <button class="filechip" onclick={open} title={item.path}>
      <Icon name={broken ? 'image-off' : 'paperclip'} size={14} /> {broken ? item.path : (item.name || item.path)}
    </button>
  {/if}
</div>
