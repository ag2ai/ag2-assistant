<script>
  // Reusable markdown renderer (deliverable assets, the full-view viewer, …).
  // Same sanitise + task-id linkify path AgentMessage uses; re-runs on text change.
  import { renderMarkdown, linkifyDom, bindImages } from '../lib/markdown.js'
  import { go, openAsideFile } from '../router.js'
  let { text } = $props()
  let el
  $effect(() => {
    if (!el) return
    el.innerHTML = renderMarkdown(text)
    linkifyDom(el, (id) => go('/t/' + id))
    bindImages(el, (i) => openAsideFile(i.path))
  })
</script>

<div class="md" bind:this={el}></div>
