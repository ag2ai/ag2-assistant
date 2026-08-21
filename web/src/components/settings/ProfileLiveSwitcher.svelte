<script lang="ts">
  // Per-profile Live (voice) model switcher (ticket 07, ADR 0015): the parallel of the
  // Text switcher for realtime voice. The composer has no Live switcher to reuse, so
  // this drives the SAME shared presentation (ModelSwitcherView) over the shared
  // `liveConfigs` store. Sets THIS profile's Active Live override via api.setLiveOverride;
  // takes effect on the NEXT voice session (voice reads the effective config fresh at
  // connect — no runtime reload).
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { SETTINGS_PAGE } from '../../store.ts'
  import { replaceOverlay } from '../../router.ts'
  import { isUsable, liveConfigs, loadLiveConfigs } from '../../lib/live.ts'
  import { PROVIDER_LABEL } from '../../lib/providerLabels.ts'
  import { getSettings } from './context.svelte.ts'
  import { m } from '../../paraglide/messages.js'
  import ModelSwitcherView from '../ModelSwitcherView.svelte'
  import type { LiveConfig } from '../../schemas/index.ts'

  const ctx = getSettings()
  onMount(() => { loadLiveConfigs().catch(() => {}) })

  const configs = $derived($liveConfigs.configs)
  const activeId = $derived(ctx.s?.live_active ?? null)
  const inherited = $derived(!ctx.s?.live_override)

  const gotoModels = () => replaceOverlay('settings', SETTINGS_PAGE.MODELS)
  const choose = (c: LiveConfig) => ctx.run(() => api.setLiveOverride(c.id))
  const useDefault = () => ctx.run(() => api.setLiveOverride(''))
</script>

<ModelSwitcherView
  {configs} {activeId} busy={ctx.busy} down {inherited}
  title={m.profile_voice_switcher_title()}
  placeholder={m.profile_choose_voice_model()}
  brandFor={(c) => c.provider}
  labelFor={(c) => `${PROVIDER_LABEL[c.provider]} · ${c.model}${c.voice ? ` · ${c.voice}` : ''}`}
  usable={isUsable}
  defaultEntry={{ label: m.profile_use_install_default(), sub: m.profile_follow_install_voice() }}
  emptyLabel={m.profile_configure_voice_model()}
  onEmpty={gotoModels}
  onChoose={choose}
  onDefault={useDefault}
  onManage={gotoModels}
  manageLabel={m.profile_manage_voice_models()}
/>
