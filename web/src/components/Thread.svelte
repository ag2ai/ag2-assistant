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

  // Autoscroll to the bottom after the DOM updates. Use $effect (not `$: tick()` —
  // that self-reschedules and loops in Svelte 5), and scroll on the next frame so
  // markdown that sets its height after render still lands us at the true bottom.
  $effect(() => {
    $thread.items
    showThinking
    requestAnimationFrame(() => { if (scroller) scroller.scrollTop = scroller.scrollHeight })
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

<div class="thread" bind:this={scroller}>
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

<Composer />
