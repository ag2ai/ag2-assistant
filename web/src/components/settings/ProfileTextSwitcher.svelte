<script lang="ts">
  // Per-profile Text model switcher (ADR 0015): sets this profile's Active Text override
  // via api.setLlmOverride (not the install-wide useLlmConfig, which only Settings →
  // Models calls, and not the per-Chat override the composer sets — ADR 0025). Reuses
  // ModelSwitcherView over the shared `llmConfigs` store for the list + env-pin/empty
  // states; the current selection + inherited-vs-overridden come from the settings
  // payload (ctx.s.llm_active / .llm_override), reloaded by ctx.run after every change.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { SETTINGS_PAGE } from '../../store.ts'
  import { replaceOverlay } from '../../router.ts'
  import { isUsable, llmConfigs, loadLlmConfigs } from '../../lib/llm.ts'
  import { typeLabel } from '../../lib/providerLabels.ts'
  import { getSettings } from './context.svelte.ts'
  import { m } from '../../paraglide/messages.js'
  import ModelSwitcherView from '../ModelSwitcherView.svelte'
  import type { LlmConfig } from '../../schemas/index.ts'

  const ctx = getSettings()
  onMount(() => { loadLlmConfigs().catch(() => {}) })

  const configs = $derived($llmConfigs.configs)
  const envOverride = $derived($llmConfigs.envOverride)
  const activeId = $derived(ctx.s?.llm_active ?? null)      // effective Active (override or install-wide)
  const inherited = $derived(!ctx.s?.llm_override)          // no per-profile override → inheriting

  const gotoModels = () => replaceOverlay('settings', SETTINGS_PAGE.MODELS)
  const choose = (c: LlmConfig) => ctx.run(() => api.setLlmOverride(c.id))
  const useDefault = () => ctx.run(() => api.setLlmOverride(''))
</script>

<ModelSwitcherView
  {configs} {activeId} {envOverride} busy={ctx.busy} down {inherited}
  title={m.profile_text_switcher_title()}
  brandFor={(c) => c.type}
  labelFor={(c) => `${typeLabel(c.type)} · ${c.model}`}
  usable={isUsable}
  defaultEntry={{ label: m.profile_use_install_default(), sub: m.profile_follow_install_model() }}
  emptyLabel={m.profile_no_models()}
  onEmpty={gotoModels}
  onChoose={choose}
  onDefault={useDefault}
  onManage={gotoModels}
  manageLabel={m.profile_manage_models()}
/>
