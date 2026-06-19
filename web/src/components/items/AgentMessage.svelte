<script>
  import { renderMarkdown, linkifyDom } from '../../lib/markdown.js'
  import { go } from '../../router.js'
  let { item } = $props()
  let el
  $effect(() => {
    if (!el) return
    el.innerHTML = renderMarkdown(item.text)        // re-runs when item.text changes (streaming)
    linkifyDom(el, (id) => go('/t/' + id))
  })
</script>

<div class="msg agent"><div class="bubble" class:voice={item.voice} class:empty={item.empty} bind:this={el}></div></div>
