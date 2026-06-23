<script>
  import { renderMarkdown, linkifyDom, bindImages } from '../../lib/markdown.js'
  import { go } from '../../router.js'
  import { thread, taskPanel, viewer } from '../../store.js'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'
  let { item } = $props()
  let el
  $effect(() => {
    if (!el) return
    el.innerHTML = renderMarkdown(item.text)        // re-runs when item.text changes (streaming)
    linkifyDom(el, (id) => go('/t/' + id))
    bindImages(el, (i) => ($viewer = { title: i.alt || i.name || 'Image', name: i.name, path: i.path }))
  })
  // Rate only finalized, non-empty replies that carry a stable key (created_at).
  const canRate = $derived(!item.streaming && !item.empty && item.at != null)
  const request = $derived(requestContext($thread.items, item, $taskPanel))
</script>

<div class="msg agent"><div class="bubble" class:voice={item.voice} class:empty={item.empty} bind:this={el}></div></div>
{#if canRate}
  <div class="itemfb"><Feedback targetKind="message" targetId={item.at} content={item.text || ''} {request} current={item.feedback} /></div>
{/if}
