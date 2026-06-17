<script>
  // Roomy full-view modal for a deliverable (or any long text), rendered as
  // markdown. Opened by setting $viewer = { title, text }.
  import { viewer } from '../store.js'
  import Markdown from './Markdown.svelte'
  const close = () => ($viewer = null)
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal viewer">
  <div class="vhead">
    <h2>{$viewer.title || 'Deliverable'}</h2>
    <button class="modal-close" onclick={close}>Close</button>
  </div>
  <div class="vbody"><Markdown text={$viewer.text} /></div>
</div>
