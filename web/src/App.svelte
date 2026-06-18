<script>
  import { route, go, newChatId } from './router.js'
  import { openThread, closeThread } from './controller.js'
  import { googleOpen, voicePickerOpen, viewer, settingsOpen, memoryOpen } from './store.js'
  import Drawer from './components/Drawer.svelte'
  import Thread from './components/Thread.svelte'
  import Hitl from './components/Hitl.svelte'
  import Google from './components/Google.svelte'
  import VoicePicker from './components/VoicePicker.svelte'
  import Viewer from './components/Viewer.svelte'
  import Settings from './components/Settings.svelte'
  import Memory from './components/Memory.svelte'

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
    <Hitl />
    {#if $route.name === 'home'}
      <div class="thread"><div class="empty"><h1>AG2 Assistant</h1>Starting a conversation…</div></div>
    {:else}
      <Thread />
    {/if}
  </div>
  {#if $settingsOpen}<Settings />{/if}
  {#if $memoryOpen}<Memory />{/if}
  {#if $googleOpen}<Google />{/if}
  {#if $voicePickerOpen}<VoicePicker />{/if}
  {#if $viewer}<Viewer />{/if}
</div>
