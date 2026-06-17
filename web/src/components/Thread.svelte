<script>
  import { thread, taskPanel } from '../store.js'
  import { go, newChatId } from '../router.js'
  import Item from './Item.svelte'
  import Composer from './Composer.svelte'
  import Thinking from './items/Thinking.svelte'
  import TaskPanel from './task/TaskPanel.svelte'

  let scroller
  const tail = $derived($thread.items[$thread.items.length - 1])
  const showThinking = $derived($thread.busy && !(tail && tail.kind === 'agent' && tail.streaming))

  // Autoscroll after the DOM updates. Use $effect (not `$: tick()`) — the legacy
  // reactive + tick() pairing self-reschedules and loops forever in Svelte 5.
  $effect(() => {
    $thread.items
    showThinking
    if (scroller) scroller.scrollTop = scroller.scrollHeight
  })
</script>

<div class="mhead">
  <button class="back" onclick={() => go('/c/' + newChatId())}>← Chat</button>
  <span class="title">
    {#if $thread.kind === 'task'}{($taskPanel && $taskPanel.title) || 'Task'}{:else}Conversation{/if}
  </span>
  {#if $thread.kind === 'task' && $taskPanel}<span class="badge">{$taskPanel.status}</span>{/if}
</div>

<div class="thread" bind:this={scroller}>
  <div class="inner">
    {#if $thread.kind === 'task'}<TaskPanel />{/if}
    {#if !$thread.items.length && $thread.kind === 'chat'}
      <div class="empty"><h1>How can I help?</h1>Ask me anything — I can search, run code, manage tasks, and more.</div>
    {/if}
    {#each $thread.items as item (item.id)}
      <Item {item} />
    {/each}
    {#if showThinking}<Thinking />{/if}
  </div>
</div>

<Composer />
