<script>
  import { onMount } from 'svelte'
  import { route, go, newChatId, redirectToProfile } from './router.js'
  import { openThread, closeThread } from './controller.js'
  import { googleOpen, codexOpen, voicePickerOpen, viewer, settingsOpen, settingsPage, memoryOpen, poweredByOpen, filesOpen, ag2View, onboardingOpen, profiles, animations, appVersion } from './store.js'
  import { api } from './transport/api.js'
  import { setActiveProfileId, storedProfileId } from './lib/profile.js'
  import { setPalette } from './design/palette.js'
  import Onboarding from './components/Onboarding.svelte'
  import Drawer from './components/Drawer.svelte'
  import Thread from './components/Thread.svelte'
  import Hitl from './components/Hitl.svelte'
  import Google from './components/Google.svelte'
  import Codex from './components/Codex.svelte'
  import VoicePicker from './components/VoicePicker.svelte'
  import Viewer from './components/Viewer.svelte'
  import Settings from './components/Settings.svelte'
  import Memory from './components/Memory.svelte'
  import Inspector from './components/Inspector.svelte'
  import PoweredBy from './components/PoweredBy.svelte'
  import Files from './components/Files.svelte'
  import Notice from './components/Notice.svelte'

  // Boot gate: nothing profile-dependent renders until we've resolved the active
  // profile. 'loading' → fetching /api/profiles; 'create' → zero profiles, run the
  // fresh-install onboarding flow (which contains the profile-creation loop, §5.5);
  // 'ready' → active pid resolved, run the app.
  let boot = $state('loading')

  // The install-level onboarding flag from the registry (§4.2). Drives whether the
  // welcome/onboarding overlay opens once a profile already exists.
  let registryOnboarded = $state(true)

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
      registryOnboarded = !!reg.onboarded
      $appVersion = reg.version || ''
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
    // §5.3 Palette ownership: the active profile's palette IS the applied palette.
    // palette.js self-applied localStorage('ag2-palette') pre-Svelte as a *hint*
    // (avoids flash); correct it from the registry now — the profile is the source
    // of truth, not localStorage. Switching is full-page nav, so boot covers it too.
    const active = list.find((p) => p.id === pid)
    if (active?.palette) setPalette(active.palette)
    // Canonicalise the URL: bare /app/ or a stale/foreign pid → /app/{pid}/.
    if ($route.pid !== pid) redirectToProfile(pid)
    boot = 'ready'
    reopenSettingsAfterSwitch()
    maybeOnboard()
  }

  // A profile switch from inside Settings reloads the SPA; the switcher stashed a
  // one-shot flag naming the page it was on. Honour it so Settings re-opens where
  // the user left it, then clear the flag (a plain refresh must NOT re-open it).
  function reopenSettingsAfterSwitch() {
    let page = null
    try {
      page = sessionStorage.getItem('ag2-reopen-settings')
      if (page) sessionStorage.removeItem('ag2-reopen-settings')
    } catch {}
    if (!page) return
    $settingsPage = page
    $settingsOpen = true
  }

  // Fresh-install onboarding finished (§5.5): it created ≥1 profile live and set
  // the install-level onboarded flag itself. The `profiles` store was populated by
  // the flow as each profile was created; adopt the first and boot into it.
  function onFreshOnboarded(firstPid) {
    const list = $profiles.list || []
    const pid = firstPid || (list[0] && list[0].id)
    if (!pid) { boot = 'loading'; return } // nothing created — shouldn't happen
    setActiveProfileId(pid)
    $profiles = { list, activeId: pid }
    const active = list.find((p) => p.id === pid)
    if (active?.palette) setPalette(active.palette)
    redirectToProfile(pid)
    boot = 'ready'
  }

  // First-run welcome overlay (the existing multi-step onboarding) — shown when
  // this install hasn't onboarded (registry flag §4.2) AND no provider key is
  // stored. Only reached when at least one profile already exists (fresh installs
  // render the onboarding flow directly via boot === 'create').
  async function maybeOnboard() {
    if (registryOnboarded) return
    try {
      const s = await api.settings()
      const anyKey = ['gemini', 'openai', 'anthropic'].some((p) => s.keys?.[p]?.set)
      if (!anyKey) $onboardingOpen = true
    } catch {}
  }

  // ⌘1..9 / Ctrl+1..9 profile shortcuts (§5.4/§5.4). Registered once here (App owns
  // the profiles store + boot). Maps the modifier + a digit to the Nth profile in
  // registry order and triggers the SAME full-page nav as a chip click. Ignored
  // when any modal is open or focus is in an editable field (typing "⌘2" in the
  // message box must not switch profiles).
  function anyModalOpen() {
    return $settingsOpen || $memoryOpen || $poweredByOpen || $filesOpen
      || $googleOpen || $codexOpen || $voicePickerOpen || !!$viewer || $onboardingOpen
  }
  function editableFocused() {
    const el = document.activeElement
    if (!el) return false
    const tag = el.tagName
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
  }
  function onProfileShortcut(e) {
    // Cmd on mac, Ctrl elsewhere; require exactly that modifier (no Shift/Alt).
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
    if (e.key < '1' || e.key > '9') return
    if (boot !== 'ready' || anyModalOpen() || editableFocused()) return
    const list = $profiles.list || []
    const target = list[Number(e.key) - 1]
    if (!target) return
    e.preventDefault()
    if (target.id !== $profiles.activeId) location.assign('/app/' + target.id + '/')
  }
  onMount(() => {
    window.addEventListener('keydown', onProfileShortcut)
    return () => window.removeEventListener('keydown', onProfileShortcut)
  })

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
  <Onboarding fresh={true} onComplete={onFreshOnboarded} />
{:else}
  <!-- data-animations lets any component's CSS gate its motion on the app-wide
       tier (see store.animations) without importing the store — same
       attribute-driven pattern as theming. -->
  <div class="app" class:ag2={showInspector} data-animations={$animations}>
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
    {#if $codexOpen}<Codex />{/if}
    {#if $voicePickerOpen}<VoicePicker />{/if}
    {#if $viewer}<Viewer />{/if}
    {#if $onboardingOpen}<Onboarding />{/if}
  </div>
{/if}

<!-- Transient recovery toast (§4.9); renders in any boot state. -->
<Notice />
