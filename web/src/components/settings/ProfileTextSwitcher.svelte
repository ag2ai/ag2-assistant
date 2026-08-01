<script>
  // Per-profile Text model switcher (ADR 0015): sets this profile's Active Text override
  // via api.setLlmOverride (not the install-wide useLlmConfig the composer calls). Reuses
  // ModelSwitcherView over the shared `llmConfigs` store for the list + env-pin/empty
  // states; the current selection + inherited-vs-overridden come from the settings
  // payload (ctx.s.llm_active / .llm_override), reloaded by ctx.run after every change.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { SETTINGS_PAGE } from '../../store.js'
  import { replaceOverlay } from '../../router.js'
  import { TYPE_LABEL, isUsable, llmConfigs, loadLlmConfigs } from '../../lib/llm.js'
  import { getSettings } from './context.svelte.js'
  import ModelSwitcherView from '../ModelSwitcherView.svelte'

  const ctx = getSettings()
  onMount(() => { loadLlmConfigs().catch(() => {}) })

  const configs = $derived($llmConfigs.configs)
  const envOverride = $derived($llmConfigs.envOverride)
  const activeId = $derived(ctx.s?.llm_active ?? null)      // effective Active (override or install-wide)
  const inherited = $derived(!ctx.s?.llm_override)          // no per-profile override → inheriting

  const gotoModels = () => replaceOverlay('settings', SETTINGS_PAGE.MODELS)
  const choose = (c) => ctx.run(() => api.setLlmOverride(c.id))
  const useDefault = () => ctx.run(() => api.setLlmOverride(''))
</script>

<ModelSwitcherView
  {configs} {activeId} {envOverride} busy={ctx.busy} down {inherited}
  title="Text model for this profile — takes effect next message"
  brandFor={(c) => c.type}
  labelFor={(c) => `${TYPE_LABEL[c.type]} · ${c.model}`}
  usable={isUsable}
  defaultEntry={{ label: 'Use install default', sub: 'Follow the install-wide Active model' }}
  emptyLabel="No models configured"
  onEmpty={gotoModels}
  onChoose={choose}
  onDefault={useDefault}
  onManage={gotoModels}
  manageLabel="Manage models…"
/>
