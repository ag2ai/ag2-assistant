<script>
  // 👍/👎 with a MANDATORY reason on a generated item. Clicking a thumb opens a
  // reason field; Save is disabled until it's non-empty (both thumbs). Sends a
  // feedback frame upstream (controller.feedback) — the server emits a FeedbackGiven
  // event (which folds back into `current`) and a learner agent distils it into memory.
  import { feedback } from '../../controller.js'
  import Icon from '../Icon.svelte'

  let { targetKind, targetId, content = '', request = '', current = null } = $props()

  let pending = $state(null) // 'up' | 'down' | null — thumb awaiting a reason
  let reason = $state('')
  let inputEl = $state(null)
  let local = $state(null)   // optimistic state until the event round-trips into `current`
  const fb = $derived(local ?? current)

  function start(sentiment) {
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
    pending = null; reason = ''
  }
  function key(e) {
    if (e.key === 'Enter') { e.preventDefault(); submit() }
    else if (e.key === 'Escape') cancel()
  }
</script>

<div class="fb">
  <div class="fb-row">
    <button class="fb-thumb" class:on={fb?.sentiment === 'up'} class:active={pending === 'up'}
            title={fb?.sentiment === 'up' ? `You liked this: ${fb.reason}` : 'Good — tell me why'}
            aria-label="Thumbs up" onclick={() => start('up')}>
      <Icon name="thumbs-up" size={14} />
    </button>
    <button class="fb-thumb" class:on={fb?.sentiment === 'down'} class:active={pending === 'down'}
            title={fb?.sentiment === 'down' ? `You disliked this: ${fb.reason}` : "Not right — tell me why"}
            aria-label="Thumbs down" onclick={() => start('down')}>
      <Icon name="thumbs-down" size={14} />
    </button>
    {#if fb && !pending}
      <span class="fb-recorded" title={fb.reason}>{fb.sentiment === 'up' ? 'Liked' : 'Disliked'} — {fb.reason}</span>
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
