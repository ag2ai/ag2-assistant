<script>
  // The composer's model switcher: a fast shortcut for the install-wide ACTIVE LLM
  // configuration — the exact same action as Settings → Models' "Use" (POST
  // /llm-configs/{id}/use), surfaced next to the input. It is NOT a per-thread or
  // per-message override: switching re-points load_config() for the whole install
  // and persists, so the change lands on the NEXT message you send (the in-flight
  // turn already built its agent from the previous config and can't switch
  // retroactively) — the button says so. (The per-PROFILE Text override lives in
  // Settings → Profiles and sets an override, not the install-wide Active — ADR 0015.)
  //
  // Reads the shared `llmConfigs` store (lib/llm.js), not a private fetch — so a
  // rename / add / active-switch made in Settings → Models shows up here live,
  // without a reload. Mutations here (choose → Use) refresh the same store, so
  // Settings sees them too. Presentation is the shared ModelSwitcherView. See
  // docs/adr/0004-shared-llm-config-store.md.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { SETTINGS_PAGE } from '../../store.js'
  import { openOverlay } from '../../router.js'
  import { LOGO, TYPE_LABEL, isUsable, llmConfigs, loadLlmConfigs } from '../../lib/llm.js'
  import ModelSwitcherView from '../ModelSwitcherView.svelte'

  let busy = $state(false)

  // A failed load just leaves the row empty-stated; the composer stays usable.
  onMount(() => { loadLlmConfigs().catch(() => {}) })

  const configs = $derived($llmConfigs.configs)
  const active = $derived($llmConfigs.active)
  const envOverride = $derived($llmConfigs.envOverride)

  async function choose(c) {
    if (busy || c.id === active) return
    busy = true
    try { await api.useLlmConfig(c.id); await loadLlmConfigs() } catch { /* keep prior active */ }
    busy = false
  }

  const openSettings = () => openOverlay('settings', SETTINGS_PAGE.MODELS)
</script>

<ModelSwitcherView
  {configs} activeId={active} {envOverride} {busy}
  title="Model for your next message"
  logoFor={(c) => LOGO[c.type]}
  labelFor={(c) => `${TYPE_LABEL[c.type]} · ${c.model}`}
  usable={isUsable}
  emptyLabel="No models configured — add one in Settings"
  onEmpty={openSettings}
  onChoose={choose}
  onManage={openSettings}
/>
