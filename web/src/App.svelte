<script lang="ts">
  import { onMount } from 'svelte'
  import { route, go, newChatId, redirectToProfile, closeAside } from './router.ts'
  import { openThread, closeThread, switchProfile } from './controller.ts'
  import { googleOpen, codexOpen, voicePickerOpen, viewer, settingsOpen, poweredByOpen, onboardingOpen, profiles, animations, appVersion, ag2Version, railWidth, previewWidth, previewExpanded, resetPreviewView, drawerWidth } from './store.ts'
  import { clampRailWidth, clampDrawerWidth } from './lib/railWidth.ts'
  import { uiLocale } from './lib/i18n.ts'
  import { m } from './paraglide/messages.js'
  import { api } from './transport/api/index.ts'
  import { setActiveProfileId, storedProfileId } from './lib/profile.ts'
  import { setAccent } from './design/palette.ts'
  import type { Profile } from './schemas/index.ts'
  import Onboarding from './components/Onboarding.svelte'
  import Drawer from './components/Drawer.svelte'
  import Thread from './components/Thread.svelte'
  import TaskPage from './components/task/TaskPage.svelte'
import AppBar from './components/AppBar.svelte'
  import Hitl from './components/Hitl.svelte'
  import Google from './components/Google.svelte'
  import Codex from './components/Codex.svelte'
  import VoicePicker from './components/VoicePicker.svelte'
  import Viewer from './components/Viewer.svelte'
  import Settings from './components/Settings.svelte'
  import Inspector from './components/Inspector.svelte'
  import PoweredBy from './components/PoweredBy.svelte'
  import Notice from './components/Notice.svelte'

  // Boot gate: nothing profile-dependent renders until we've resolved the active
  // profile. 'loading' → fetching /api/profiles; 'create' → zero profiles, run the
  // fresh-install onboarding flow (which contains the profile-creation loop, §5.5);
  // 'ready' → active pid resolved, run the app.
  let boot = $state<'loading' | 'create' | 'ready'>('loading')

  // The install-level onboarding flag from the registry (§4.2). Drives whether the
  // welcome/onboarding overlay opens once a profile already exists.
  let registryOnboarded = $state(true)

  // The AG2 Inspector occupies the rail when the route's `aside` is `inspector`.
  const showInspector = $derived(boot === 'ready' && $route.aside?.kind === 'inspector')

  // The preview rail's occupant: a URL-addressed file (route `aside`) or the path-less
  // transient body ($viewer). It shares the grid's right column with the Inspector and
  // takes precedence; `showRail` gates whether the third column is present.
  const railFile = $derived($route.aside?.kind === 'file')
  const railOpen = $derived(railFile || !!$viewer)
  const showRail = $derived(railOpen || showInspector)

  // The third column's width tracks the mounted occupant: the preview's own width, or
  // the Inspector's. Expanded preview fills the Thread column instead (.app.rail.railfull).
  const railW = $derived(railOpen ? clampRailWidth($previewWidth) : clampRailWidth($railWidth))
  const previewFull = $derived(railOpen && $previewExpanded)

  // Boot sequence (§7 Phase 1 item 4): fetch /api/profiles FIRST. Empty →
  // create-first-profile form. Else resolve active pid (localStorage if still
  // valid, else active_default), persist it, redirect a bare /app/ into
  // /app/{pid}/, THEN let the normal boot proceed.
  onMount(async () => {
    // Preview sizing only survives a reload when the URL re-mounts the preview
    // (aside=file); any other boot state drops a stale expanded/width.
    if ($route.aside?.kind !== 'file') resetPreviewView()
    try {
      const reg = await api.profiles()
      const list = reg.profiles
      registryOnboarded = reg.onboarded
      $appVersion = reg.version
      $ag2Version = reg.ag2_version
      $profiles = { list, activeId: null }
      if (!list.length) { boot = 'create'; return }
      resolveActive(list, reg.active_default)
    } catch {
      // Gateway unreachable — fail open into the app shell so the error surfaces
      // in the thread rather than a blank screen.
      boot = 'ready'
    }
  })

  function resolveActive(list: Profile[], activeDefault: string | null) {
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
    // §5.3 Accent ownership: the active profile's accent IS the applied accent.
    // palette.js self-applied localStorage('ag2-accent') pre-Svelte as a *hint*
    // (avoids flash); correct it from the registry now — the profile is the source
    // of truth, not localStorage. Switching is full-page nav, so boot covers it too.
    const active = list.find((p) => p.id === pid)
    if (active?.accent) setAccent(active.accent)
    // Canonicalise the URL: bare /app/ or a stale/foreign pid → /app/{pid}/. This
    // preserves the hash (redirectToProfile), so a `#settings=<section>` carried
    // across a profile-switch/archive reload — or a cold deep-link — survives and
    // reopens Settings on the same Section (the URL is the source of truth; no
    // sessionStorage reopen flag anymore).
    if ($route.pid !== pid) redirectToProfile(pid)
    boot = 'ready'
    maybeOnboard()
  }

  // Fresh-install onboarding finished (§5.5): it created ≥1 profile live and set
  // the install-level onboarded flag itself. The `profiles` store was populated by
  // the flow as each profile was created; adopt the first and boot into it.
  function onFreshOnboarded(firstPid: string | null) {
    const list = $profiles.list
    const pid = firstPid || (list[0] && list[0].id)
    if (!pid) { boot = 'loading'; return } // nothing created — shouldn't happen
    setActiveProfileId(pid)
    $profiles = { list, activeId: pid }
    const active = list.find((p) => p.id === pid)
    if (active?.accent) setAccent(active.accent)
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
      const anyKey = ['gemini', 'openai', 'anthropic'].some((p) => s.keys[p]?.set)
      if (!anyKey) $onboardingOpen = true
    } catch {}
  }

  // ⌘1..9 / Ctrl+1..9 profile shortcuts (§5.4/§5.4). Registered once here (App owns
  // the profiles store + boot). Maps the modifier + a digit to the Nth profile in
  // registry order and triggers the SAME full-page nav as a chip click. Ignored
  // when any modal is open or focus is in an editable field (typing "⌘2" in the
  // message box must not switch profiles).
  // The preview rail is shell navigation, not a modal, so it's excluded here — the
  // ⌘/Ctrl-1..9 profile shortcuts keep firing while a preview is open.
  function anyModalOpen() {
    return $settingsOpen || $poweredByOpen
      || $googleOpen || $codexOpen || $voicePickerOpen || $onboardingOpen
  }
  function editableFocused() {
    const el = document.activeElement
    if (!el) return false
    const tag = el.tagName
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
      || (el instanceof HTMLElement && el.isContentEditable)
  }
  function onProfileShortcut(e: KeyboardEvent) {
    // Cmd on mac, Ctrl elsewhere; require exactly that modifier (no Shift/Alt).
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
    if (e.key < '1' || e.key > '9') return
    if (boot !== 'ready' || anyModalOpen() || editableFocused()) return
    const list = $profiles.list
    const target = list[Number(e.key) - 1]
    if (!target) return
    e.preventDefault()
    switchProfile(target.id)   // in-place (no reload); no-ops on the active one
  }
  // Escape closes the preview rail when it's the topmost dismissible surface.
  // Skipped when a modal is stacked over it (the modal owns Escape) or focus is in
  // an editable field (a rail markdown edit, the composer) — same guards as the
  // profile shortcuts, since the rail is shell navigation living at this level.
  // A URL-addressed file strips the aside key (via closeAside → confirmDiscard guard);
  // a path-less transient body just clears its store.
  function onEscape(e: KeyboardEvent) {
    if (e.key !== 'Escape' || !railOpen || anyModalOpen() || editableFocused()) return
    e.preventDefault()
    if (railFile) closeAside()
    else $viewer = null
  }
  onMount(() => {
    window.addEventListener('keydown', onProfileShortcut)
    window.addEventListener('keydown', onEscape)
    return () => {
      window.removeEventListener('keydown', onProfileShortcut)
      window.removeEventListener('keydown', onEscape)
    }
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
    // A 'run'/'chat' route always carries an id (the path regex requires it), so a
    // missing one means the URL isn't a thread — it falls through to the fresh chat.
    if (r.name === 'run' && r.id) openThread('run', r.id)
    else if (r.name === 'chat' && r.id) openThread('chat', r.id)
    else if (r.name === 'task' || r.name === 'tasks' || r.name === 'files') closeThread()
    else { closeThread(); go('/c/' + newChatId()) } // home → a fresh chat
  })
</script>

{#key $uiLocale}
{#if boot === 'loading'}
  <div class="app"><div class="main"><div class="thread"><div class="empty"><h1>AG2 Assistant</h1>{m.loading()}</div></div></div></div>
{:else if boot === 'create'}
  <Onboarding fresh={true} onComplete={onFreshOnboarded} />
{:else}
  <!-- data-animations lets any component's CSS gate its motion on the app-wide
       tier (see store.animations) without importing the store — same
       attribute-driven pattern as theming. -->
  <div class="app" class:ag2={showInspector} class:rail={showRail} class:railfull={previewFull}
       style="--drawer-w: {clampDrawerWidth($drawerWidth)}px; --rail-w: {railW}px" data-animations={$animations}>
    <Drawer />
    <div class="main">
      <Hitl />
      {#if $route.name === 'chat' || $route.name === 'run'}
        <Thread />
      {:else if $route.name === 'task'}
        <TaskPage />
      {:else if $route.name === 'files'}
        <AppBar title={m.tab_files()} />
        <div class="thread"><div class="empty"><h1>{m.tab_files()}</h1>{m.files_empty_hint()}</div></div>
      {:else if $route.name === 'tasks'}
        <AppBar title={m.tab_tasks()} />
        <div class="thread"><div class="empty"><h1>{m.tab_tasks()}</h1>{m.tasks_empty_hint()}</div></div>
      {:else}
        <AppBar title="AG2 Assistant" />
        <div class="thread"><div class="empty"><h1>AG2 Assistant</h1>{m.starting_conversation()}</div></div>
      {/if}
    </div>
    <!-- The right rail holds one occupant; the preview takes precedence over the
         Inspector. Both share the grid's third column. -->
    {#if railOpen}
      <Viewer />
    {:else if showInspector}
      <Inspector />
    {/if}
    {#if $settingsOpen}<Settings />{/if}
    {#if $poweredByOpen}<PoweredBy />{/if}
    {#if $googleOpen}<Google />{/if}
    {#if $codexOpen}<Codex />{/if}
    {#if $voicePickerOpen}<VoicePicker />{/if}
    {#if $onboardingOpen}<Onboarding />{/if}
  </div>
{/if}

<!-- Transient recovery toast (§4.9); renders in any boot state. -->
<Notice />
<!-- Messages are plain function calls, so a UI-language switch re-renders by
     re-keying the whole tree (the sanctioned Paraglide SPA pattern) — {/key}
     closes the block opened before the boot gate. -->
{/key}
