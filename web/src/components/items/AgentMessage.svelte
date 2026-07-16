<script>
  import { renderMarkdown, linkifyDom, bindImages } from '../../lib/markdown.js'
  import { a2uiComposingSurfaceId, splitA2UIText } from '../../lib/a2ui.js'
  import A2UIComposing from './A2UIComposing.svelte'
  import { go } from '../../router.js'
  import { thread, taskPanel, viewer } from '../../store.js'
  import { requestContext } from '../../lib/feedback.js'
  import Feedback from './Feedback.svelte'
  let { item } = $props()
  // The model writes A2UI messages into its reply text. That payload is never prose,
  // so it's stripped — and while it's still being typed, a placeholder stands in for
  // the surface it will become (rather than a wall of half-finished JSON).
  const parsed = $derived(splitA2UIText(item.text || ''))
  const displayText = $derived(parsed.text)
  const composing = $derived(!!item.streaming && parsed.composing)
  const composingSurfaceId = $derived(a2uiComposingSurfaceId(item.text || ''))
  const replacesCanvas = $derived(
    !!composingSurfaceId && $thread.items.some((entry) => entry.kind === 'a2ui' && entry.surfaceId === composingSurfaceId)
  )
  let el
  $effect(() => {
    if (!el) return
    el.innerHTML = renderMarkdown(displayText)        // re-runs when item.text changes (streaming)
    linkifyDom(el, (id) => go('/t/' + id))
    bindImages(el, (i) => ($viewer = { title: i.alt || i.name || 'Image', name: i.name, path: i.path }))
  })
  // Rate only finalized, non-empty replies that carry a stable key (created_at).
  const canRate = $derived(!item.streaming && !item.empty && displayText && item.at != null)
  const request = $derived(requestContext($thread.items, item, $taskPanel))
</script>

<!-- An A2UI-only reply (payload, no prose) has nothing to put in the bubble yet. -->
{#if displayText || !composing}
  <div class="msg agent"><div class="bubble" class:voice={item.voice} class:empty={item.empty} bind:this={el}></div></div>
{/if}
{#if composing && !replacesCanvas}
  <A2UIComposing />
{/if}
{#if canRate}
  <div class="itemfb"><Feedback targetKind="message" targetId={item.at} content={displayText} {request} current={item.feedback} /></div>
{/if}
