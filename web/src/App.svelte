<script>
  import { onMount } from 'svelte'
  import { route, go, newChatId } from './router.js'
  import { openThread, closeThread } from './controller.js'
  import { googleOpen, voicePickerOpen, viewer, settingsOpen, memoryOpen, poweredByOpen, filesOpen, ag2View, onboardingOpen, onboarded } from './store.js'
  import { api } from './transport/api.js'
  import Onboarding from './components/Onboarding.svelte'
  import Drawer from './components/Drawer.svelte'
  import Thread from './components/Thread.svelte'
  import Hitl from './components/Hitl.svelte'
  import Google from './components/Google.svelte'
  import VoicePicker from './components/VoicePicker.svelte'
  import Viewer from './components/Viewer.svelte'
  import Settings from './components/Settings.svelte'
  import Memory from './components/Memory.svelte'
  import Inspector from './components/Inspector.svelte'
  import PoweredBy from './components/PoweredBy.svelte'
  import Files from './components/Files.svelte'

  // The AG2 Inspector occupies a right rail when AG2 view is on and a thread is open.
  const showInspector = $derived($ag2View && $route.name !== 'home')

  // First-run welcome: show onboarding when the user hasn't completed it AND no
  // provider key is stored yet. Skipped if the gateway isn't reachable.
  onMount(async () => {
    if ($onboarded) return
    try {
      const s = await api.settings()
      const anyKey = ['gemini', 'openai', 'anthropic'].some((p) => s.keys?.[p]?.set)
      if (!anyKey) $onboardingOpen = true
    } catch {}
  })

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

<div class="app" class:ag2={showInspector}>
  <Drawer />
  <div class="main">
    <Hitl />
    {#if $route.name === 'home'}
      <div class="thread"><div class="empty"><h1>AG2 Assistant</h1>Starting a conversation…</div></div>
    {:else}
      <Thread />
    {/if}
  </div>
  {#if showInspector}<Inspector />{/if}
  {#if $settingsOpen}<Settings />{/if}
  {#if $memoryOpen}<Memory />{/if}
  {#if $poweredByOpen}<PoweredBy />{/if}
  {#if $filesOpen}<Files />{/if}
  {#if $googleOpen}<Google />{/if}
  {#if $voicePickerOpen}<VoicePicker />{/if}
  {#if $viewer}<Viewer />{/if}
  {#if $onboardingOpen}<Onboarding />{/if}
</div>
