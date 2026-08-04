<script lang="ts">
  import { renderMarkdown, linkifyDom, bindImages } from '../../lib/markdown.ts'
  import { a2uiComposingSurfaceId, splitA2UIText } from '../../lib/a2ui.ts'
  import A2UIComposing from './A2UIComposing.svelte'
  import { go, openAsideFile } from '../../router.ts'
  import { thread, runInfo } from '../../store.ts'
  import { requestContext } from '../../lib/feedback.ts'
  import Feedback from './Feedback.svelte'
  import type { ThreadItem } from '../../schemas/events.ts'

  type Props = { item: Extract<ThreadItem, { kind: 'agent' }> }
  let { item }: Props = $props()
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
  // bind:this target: the effect below reads it, so it has to be reactive.
  let el: HTMLDivElement | undefined = $state()
  $effect(() => {
    if (!el) return
    el.innerHTML = renderMarkdown(displayText)        // re-runs when item.text changes (streaming)
    linkifyDom(el, (id) => go('/t/' + id))
    bindImages(el, (i) => openAsideFile(i.path))
  })
  // Rate only finalized, non-empty replies that carry a stable key (created_at) —
  // which is also the feedback target id, so the two travel together.
  const rateKey = $derived(!item.streaming && !item.empty && displayText ? item.at : null)
  const request = $derived(requestContext($thread.items, item, $runInfo))
</script>

<!-- An A2UI-only reply (payload, no prose) has nothing to put in the bubble yet. -->
{#if displayText || !composing}
  <div class="msg agent"><div class="bubble" class:voice={item.voice} class:empty={item.empty} bind:this={el}></div></div>
{/if}
{#if composing && !replacesCanvas}
  <A2UIComposing />
{/if}
{#if rateKey != null}
  <div class="itemfb"><Feedback targetKind="message" targetId={rateKey} content={displayText} {request} current={item.feedback} /></div>
{/if}
