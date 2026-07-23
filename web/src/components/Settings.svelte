<script>
  // Settings — thin shell. Owns the modal chrome, the sidebar nav, and the page
  // switch; all section markup lives in the six settings/*Page.svelte components,
  // which share one reactive $state context (settings/context.svelte.js).
  import { profiles, profileEpoch, SETTINGS_PAGE, voicePickerOpen, codexOpen } from '../store.js'
  import { route, replaceOverlay } from '../router.js'
  import { getActiveProfileId } from '../lib/profile.js'
  import { createSettingsContext } from './settings/context.svelte.js'
  import GeneralPage from './settings/GeneralPage.svelte'
  import ProfilesPage from './settings/ProfilesPage.svelte'
  import ModelsPage from './settings/ModelsPage.svelte'
  import SecretsPage from './settings/SecretsPage.svelte'
  import ToolsPage from './settings/ToolsPage.svelte'
  import IntegrationsPage from './settings/IntegrationsPage.svelte'
  import AdvancedPage from './settings/AdvancedPage.svelte'

  const PAGES = [
    { id: SETTINGS_PAGE.GENERAL, label: 'General', comp: GeneralPage },
    { id: SETTINGS_PAGE.PROFILES, label: 'Profiles', comp: ProfilesPage },
    { id: SETTINGS_PAGE.MODELS, label: 'Models', comp: ModelsPage },
    { id: SETTINGS_PAGE.SECRETS, label: 'Secrets', comp: SecretsPage },
    { id: SETTINGS_PAGE.TOOLS, label: 'Tools & Permissions', comp: ToolsPage },
    { id: SETTINGS_PAGE.INTEGRATIONS, label: 'Integrations', comp: IntegrationsPage },
    { id: SETTINGS_PAGE.ADVANCED, label: 'Advanced', comp: AdvancedPage },
  ]

  // Must run synchronously at init — setContext requires it (see context header).
  const ctx = createSettingsContext()

  // The active Section is DERIVED from the route (the URL hash is the single source
  // of truth); validate against PAGES, fallback General. A Section click REPLACEs the
  // hash (no per-click history spam) → route updates → this re-derives.
  const page = $derived(PAGES.some((p) => p.id === $route.overlayValue) ? $route.overlayValue : SETTINGS_PAGE.GENERAL)
  function select(id) { replaceOverlay('settings', id) }

  // Svelte 5 renders a capitalized component-valued variable directly as <Active />.
  const Active = $derived((PAGES.find((p) => p.id === page) || PAGES[0]).comp)

  // Title the modal with the active profile — settings (model, MCP, folder, …) are
  // per-profile, so it's clear which one you're configuring (§5.4).
  const activeName = $derived.by(() => {
    const id = $profiles.activeId || getActiveProfileId()
    return ($profiles.list || []).find((p) => p.id === id)?.name || ''
  })

  // Load on mount, and reload on each in-place profile switch (profileEpoch bumps)
  // so the open modal shows the new profile's settings, not the previous one's.
  $effect(() => { $profileEpoch; ctx.load() })

  // Esc closes Settings — symmetric with the × button and browser Back (all funnel
  // through ctx.close → closeOverlay, stripping the hash). Guarded so Esc dismisses a
  // child modal stacked OVER Settings (the voice picker / Codex sign-in) first, not
  // the Settings shell underneath it.
  function onKey(e) {
    if (e.key === 'Escape' && !$voicePickerOpen && !$codexOpen) ctx.close()
  }
</script>

<svelte:window onkeydown={onKey} />
<div class="modal-backdrop" onclick={ctx.close}></div>
<div class="modal settings">
  <button class="modal-x" aria-label="Close" onclick={ctx.close}>×</button>
  <h2>Settings{activeName ? ' — ' + activeName : ''}</h2>
  {#if ctx.err}<p class="muted" style="color:var(--danger)">{ctx.err}</p>{/if}

  {#if !ctx.s}
    <p class="muted">Loading…</p>
  {:else}
    <div class="setbody">
      <nav class="setnav">
        {#each PAGES as p}
          <button class="setnavbtn" class:on={page === p.id} onclick={() => select(p.id)}>{p.label}</button>
        {/each}
      </nav>
      <div class="setscroll">
        <Active />
      </div>
    </div>
  {/if}
</div>
