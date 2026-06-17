<script>
  // Reusable markdown renderer (deliverable assets, the full-view viewer, …).
  // Same sanitise + task-id linkify path AgentMessage uses; re-runs on text change.
  import { renderMarkdown, linkifyDom } from '../lib/markdown.js'
  import { go } from '../router.js'
  let { text } = $props()
  let el
  $effect(() => {
    if (!el) return
    el.innerHTML = renderMarkdown(text)
    linkifyDom(el, (id) => go('/t/' + id))
  })
</script>

<div class="md" bind:this={el}></div>
