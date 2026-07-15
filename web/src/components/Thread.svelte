<script>
  import { thread, taskPanel, ag2View, profile } from '../store.js'
  import { go, newChatId } from '../router.js'
  import Item from './Item.svelte'
  import Composer from './Composer.svelte'
  import Thinking from './items/Thinking.svelte'
  import TaskPanel from './task/TaskPanel.svelte'
  import Icon from './Icon.svelte'
  import ThemeToggle from './ThemeToggle.svelte'
  import SystemHealth from './SystemHealth.svelte'

  let scroller
  const tail = $derived($thread.items[$thread.items.length - 1])
  const showThinking = $derived($thread.busy && !(tail && tail.kind === 'agent' && tail.streaming))

  // Autoscroll follows the stream only while the reader is at the bottom. Scrolling
  // up unpins (so you can read back mid-turn); scrolling back down re-pins. Without
  // this, every streamed chunk yanks the view back down and reading is impossible.
  const NEAR_BOTTOM = 80 // px of slack — "at the bottom" for a reader, not to the pixel
  let pinned = $state(true)
  function onScroll() {
    if (scroller) pinned = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < NEAR_BOTTOM
  }
  // Glide to the latest and re-pin (so the stream follows again). A hand-rolled eased
  // tween (not native `behavior:'smooth'`, which is browser-paced and whips over long
  // distances) with a capped duration. The streaming autoscroll below stays instant so
  // it can't fight each chunk.
  function scrollToBottom() {
    if (!scroller) return
    const el = scroller
    const start = el.scrollTop
    const dist = (el.scrollHeight - el.clientHeight) - start
    if (dist <= 4) { el.scrollTop = el.scrollHeight; pinned = true; return }
    const dur = Math.min(600, 240 + dist * 0.3) // ms — longer for farther, capped
    const ease = (p) => 1 - Math.pow(1 - p, 3)   // easeOutCubic
    let t0 = null
    function step(ts) {
      if (t0 === null) t0 = ts
      const p = Math.min(1, (ts - t0) / dur)
      el.scrollTop = start + dist * ease(p)
      if (p < 1) requestAnimationFrame(step)
      else pinned = true
    }
    requestAnimationFrame(step)
  }

  // Opening another thread starts pinned again, whatever the last one was left at.
  let shown = null
  $effect(() => {
    if ($thread.id !== shown) {
      shown = $thread.id
      pinned = true
    }
  })

  // Sending re-pins: you asked for the reply, so follow it. (`sent` is a plain let,
  // not $state — this effect must not invalidate itself.)
  let sent = null
  $effect(() => {
    if (tail && tail.kind === 'user' && tail.id !== sent) {
      sent = tail.id
      pinned = true
    }
  })

  // Autoscroll to the bottom after the DOM updates. Use $effect (not `$: tick()` —
  // that self-reschedules and loops in Svelte 5), and scroll on the next frame so
  // markdown that sets its height after render still lands us at the true bottom.
  $effect(() => {
    $thread.items
    showThinking
    if (!pinned) return
    requestAnimationFrame(() => { if (scroller && pinned) scroller.scrollTop = scroller.scrollHeight })
  })
</script>

<div class="mhead">
  <button class="back" onclick={() => go('/c/' + newChatId())}><Icon name="chevron-left" size={15} /> Chat</button>
  <span class="title">
    {#if $thread.kind === 'task'}{($taskPanel && $taskPanel.title) || 'Task'}{:else}Conversation{/if}
  </span>
  {#if $thread.kind === 'task' && $taskPanel}<span class="badge">{$taskPanel.status}</span>{/if}
  <div class="hactions">
    <SystemHealth />
    <ThemeToggle />
    <button class="ag2toggle" class:on={$ag2View} class:ag2-glow={$ag2View} onclick={() => ($ag2View = !$ag2View)}
            title="AG2 view — reveal the live AG2 events powering the UI"><Icon name="code" size={14} /> AG2</button>
  </div>
</div>

<div class="thread" bind:this={scroller} onscroll={onScroll}>
  <div class="inner">
    {#if $thread.kind === 'task'}<TaskPanel />{/if}
    {#if !$thread.items.length && $thread.kind === 'chat'}
      <div class="empty"><h1>How can I help{$profile.name ? `, ${$profile.name}` : ''}?</h1>Ask me anything — I can search, run code, manage tasks, and more.</div>
    {/if}
    {#each $thread.items as item (item.id)}
      <Item {item} />
    {/each}
    {#if showThinking}<Thinking />{/if}
  </div>
</div>

{#if !pinned}
  <button class="scrolldown" onclick={scrollToBottom} title="Scroll to latest" aria-label="Scroll to latest">
    <Icon name="chevron-down" size={18} />
  </button>
{/if}

<Composer />
