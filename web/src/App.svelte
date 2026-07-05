<script>
  import { onMount } from 'svelte'
  import { route, go, newChatId, redirectToProfile } from './router.js'
  import { openThread, closeThread } from './controller.js'
  import { googleOpen, voicePickerOpen, viewer, settingsOpen, memoryOpen, poweredByOpen, filesOpen, ag2View, onboardingOpen, profiles } from './store.js'
  import { api } from './transport/api.js'
  import { setActiveProfileId, storedProfileId } from './lib/profile.js'
  import Onboarding from './components/Onboarding.svelte'
  import ProfileCreate from './components/ProfileCreate.svelte'
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

  // Boot gate: nothing profile-dependent renders until we've resolved the active
  // profile. 'loading' → fetching /api/profiles; 'create' → zero profiles, show
  // the create-first-profile form; 'ready' → active pid resolved, run the app.
  let boot = $state('loading')

  // The AG2 Inspector occupies a right rail when AG2 view is on and a thread is open.
  const showInspector = $derived(boot === 'ready' && $ag2View && $route.name !== 'home')

  // Boot sequence (§7 Phase 1 item 4): fetch /api/profiles FIRST. Empty →
  // create-first-profile form. Else resolve active pid (localStorage if still
  // valid, else active_default), persist it, redirect a bare /app/ into
  // /app/{pid}/, THEN let the normal boot proceed.
  onMount(async () => {
    try {
      const reg = await api.profiles()
      const list = reg.profiles || []
      $profiles = { list, activeId: null }
      if (!list.length) { boot = 'create'; return }
      resolveActive(list, reg.active_default)
    } catch {
      // Gateway unreachable — fail open into the app shell so the error surfaces
      // in the thread rather than a blank screen.
      boot = 'ready'
    }
  })

  function resolveActive(list, activeDefault) {
    const ids = new Set(list.map((p) => p.id))
    // Precedence: a VALID URL pid wins (deep links / refreshes land in the
    // profile the URL names, and it becomes the new persisted choice) → else
    // localStorage if still valid → else active_default → else first. An
    // invalid/unknown URL pid falls through the same chain and is canonicalised
    // below via redirectToProfile.
    const urlPid = $route.pid
    const stored = storedProfileId()
    const pid = (urlPid && ids.has(urlPid)) ? urlPid
      : (stored && ids.has(stored)) ? stored
      : (activeDefault && ids.has(activeDefault)) ? activeDefault
      : list[0].id
    setActiveProfileId(pid)
    $profiles = { list, activeId: pid }
    // Canonicalise the URL: bare /app/ or a stale/foreign pid → /app/{pid}/.
    if ($route.pid !== pid) redirectToProfile(pid)
    boot = 'ready'
    maybeOnboard()
  }

  // The zero-profile form succeeded: adopt the new profile and start the app.
  function onProfileCreated(profile) {
    const list = [...($profiles.list || []), profile]
    setActiveProfileId(profile.id)
    $profiles = { list, activeId: profile.id }
    redirectToProfile(profile.id)
    boot = 'ready'
  }

  // First-run welcome overlay (the existing multi-step onboarding) — shown when
  // this install hasn't onboarded AND no provider key is stored. Only reached
  // when at least one profile already exists (fresh installs go through the
  // create-first-profile form, which sets the onboarded flag itself).
  async function maybeOnboard() {
    try {
      const s = await api.settings()
      const anyKey = ['gemini', 'openai', 'anthropic'].some((p) => s.keys?.[p]?.set)
      if (!s.onboarded && !anyKey) $onboardingOpen = true
    } catch {}
  }

  // React to route changes: open the matching thread. Gated on boot === 'ready'
  // so we don't create chats before the active profile is known. $effect tracks
  // $route + boot only (writing `last` is untracked), so this can't self-invalidate.
  let last = ''
  $effect(() => {
    if (boot !== 'ready') return
    const r = $route
    const key = r.name + ':' + (r.id || '')
    if (key === last) return
    last = key
    if (r.name === 'task') openThread('task', r.id)
    else if (r.name === 'chat') openThread('chat', r.id)
    else { closeThread(); go('/c/' + newChatId()) }
  })
</script>

{#if boot === 'loading'}
  <div class="app"><div class="main"><div class="thread"><div class="empty"><h1>AG2 Assistant</h1>Loading…</div></div></div></div>
{:else if boot === 'create'}
  <ProfileCreate onCreated={onProfileCreated} />
{:else}
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
{/if}
