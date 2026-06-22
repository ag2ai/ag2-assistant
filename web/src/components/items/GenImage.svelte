<script>
  // An image the agent generated/edited — shown inline as a thumbnail; click for the
  // full-size preview. The bytes live in the workspace (served via /api/files/raw).
  import { viewer } from '../../store.js'
  import { api } from '../../transport/api.js'

  let { item } = $props()
  const open = () =>
    ($viewer = { title: item.prompt || 'Image', name: (item.path || '').split('/').pop(), path: item.path })
</script>

<div class="genimage">
  <img class="thumb" src={api.fileUrl(item.path)} alt={item.prompt || 'generated image'}
       title={item.prompt || ''} onclick={open} />
</div>
