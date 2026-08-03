<script lang="ts">
  // 👍/👎 with a MANDATORY reason on a generated item. Clicking a thumb opens a
  // reason field; Save is disabled until it's non-empty (both thumbs). Sends a
  // feedback frame upstream (controller.feedback) — the server emits a FeedbackGiven
  // event (which folds back into `current`) and a learner agent distils it into memory.
  import { feedback, clearFeedback } from '../../controller.ts'
  import Icon from '../Icon.svelte'
  import type { Feedback as FeedbackMark } from '../../schemas/events.ts'

  type Sentiment = 'up' | 'down'
  // `targetId` is whatever keys the rated item — a created_at stamp for a message,
  // an id or path elsewhere; it goes upstream as a string.
  type Props = {
    targetKind: string
    targetId: string | number
    content?: string
    request?: string
    current?: FeedbackMark
  }
  let { targetKind, targetId, content = '', request = '', current = null }: Props = $props()

  let pending: Sentiment | null = $state(null) // thumb awaiting a reason
  let reason = $state('')
  let inputEl: HTMLInputElement | null = $state(null)
  let local: FeedbackMark = $state(null)   // optimistic mark until the event round-trips into `current`
  let cleared = $state(false) // optimistic retraction until FeedbackCleared round-trips
  let noted = $state(false)   // brief "rating removed" note — only when a reason was learned
  const fb = $derived(cleared ? null : (local ?? current))

  let copied = $state(false) // brief ✓ after copying the reply text
  async function copy() {
    try {
      await navigator.clipboard.writeText(content || '')
      copied = true
      setTimeout(() => (copied = false), 1400)
    } catch { /* clipboard blocked — no-op */ }
  }

  // Clicking a thumb: the active one toggles off (unmark); any other opens the reason
  // editor to mark or switch sentiment.
  function thumb(sentiment: Sentiment) {
    if (fb && fb.sentiment === sentiment && !pending) clear()
    else start(sentiment)
  }
  function start(sentiment: Sentiment) {
    pending = sentiment
    reason = (fb && fb.sentiment === sentiment ? fb.reason : '') || ''
    queueMicrotask(() => inputEl && inputEl.focus())
  }
  function cancel() { pending = null; reason = '' }
  function submit() {
    const r = reason.trim()
    if (!r || !pending) return
    feedback({ targetKind, targetId: String(targetId), sentiment: pending, reason: r, content, request })
    local = { sentiment: pending, reason: r }
    cleared = false; noted = false
    pending = null; reason = ''
  }
  // Retract the rating. Clears only the visible thumb; learned memory is left as-is —
  // so if a reason had been distilled, say so briefly (points at Settings → Memory).
  function clear() {
    const hadReason = !!(fb && fb.reason)
    clearFeedback({ targetKind, targetId: String(targetId) })
    cleared = true; local = null; pending = null; reason = ''
    if (hadReason) { noted = true; setTimeout(() => (noted = false), 3200) }
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'Enter') { e.preventDefault(); submit() }
    else if (e.key === 'Escape') cancel()
  }
</script>

<div class="fb">
  <div class="fb-row">
    <button class="fb-thumb" class:on={copied}
            title={copied ? 'Copied' : 'Copy'} aria-label="Copy message" onclick={copy}>
      <Icon name={copied ? 'check' : 'copy'} size={14} />
    </button>
    <button class="fb-thumb" class:on={fb?.sentiment === 'up'} class:active={pending === 'up'}
            title={fb?.sentiment === 'up' ? `You liked this: ${fb.reason} — click to remove` : 'Good — tell me why'}
            aria-label="Thumbs up" onclick={() => thumb('up')}>
      <Icon name="thumbs-up" size={14} />
    </button>
    <button class="fb-thumb" class:on={fb?.sentiment === 'down'} class:active={pending === 'down'}
            title={fb?.sentiment === 'down' ? `You disliked this: ${fb.reason} — click to remove` : "Not right — tell me why"}
            aria-label="Thumbs down" onclick={() => thumb('down')}>
      <Icon name="thumbs-down" size={14} />
    </button>
    {#if fb && !pending}
      <span class="fb-recorded" title={fb.reason}>{fb.sentiment === 'up' ? 'Liked' : 'Disliked'} — {fb.reason}</span>
    {:else if noted}
      <span class="fb-recorded">Rating removed — what I learned stays in Memory</span>
    {/if}
  </div>
  {#if pending}
    <div class="fb-reason">
      <input bind:this={inputEl} bind:value={reason} onkeydown={key}
             placeholder={pending === 'up' ? 'What did you like? (required)' : "What didn't work? (required)"} />
      <button class="fb-save" disabled={!reason.trim()} onclick={submit}>Save</button>
      <button class="fb-x" onclick={cancel} aria-label="Cancel"><Icon name="x" size={13} /></button>
    </div>
  {/if}
</div>
