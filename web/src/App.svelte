<script>
  import { route, go, newChatId } from './router.js'
  import { openThread, closeThread } from './controller.js'
  import Drawer from './components/Drawer.svelte'
  import Thread from './components/Thread.svelte'

  // React to route changes: open the matching thread. $effect tracks $route only
  // (writing `last` is untracked), so this can't self-invalidate.
  let last = ''
  $effect(() => {
    const r = $route
    const key = r.name + ':' + (r.id || '')
    if (key === last) return
    last = key
    if (r.name === 'task') openThread('task', r.id)
    else if (r.name === 'chat') openThread('chat', r.id)
    else { closeThread(); go('/c/' + newChatId()) }
  })
</script>

<div class="app">
  <Drawer />
  <div class="main">
    {#if $route.name === 'home'}
      <div class="thread"><div class="empty"><h1>AGClaw</h1>Starting a conversation…</div></div>
    {:else}
      <Thread />
    {/if}
  </div>
</div>
