<script>
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
  import { LOGO, PROVIDER_LABEL, isUsable, liveConfigs, loadLiveConfigs } from '../../lib/live.js'
  import { getSettings } from './context.svelte.js'
  import ModelSwitcherView from '../ModelSwitcherView.svelte'

  const ctx = getSettings()
  onMount(() => { loadLiveConfigs().catch(() => {}) })

  const configs = $derived($liveConfigs.configs)
  const activeId = $derived(ctx.s?.live_active ?? null)
  const inherited = $derived(!ctx.s?.live_override)

  const gotoModels = () => replaceOverlay('settings', SETTINGS_PAGE.MODELS)
  const choose = (c) => ctx.run(() => api.setLiveOverride(c.id))
  const useDefault = () => ctx.run(() => api.setLiveOverride(''))
</script>

<ModelSwitcherView
  {configs} {activeId} busy={ctx.busy} down {inherited}
  title="Voice model for this profile — takes effect next voice session"
  placeholder="Choose a voice model"
  logoFor={(c) => LOGO[c.provider]}
  labelFor={(c) => `${PROVIDER_LABEL[c.provider]} · ${c.model}${c.voice ? ` · ${c.voice}` : ''}`}
  usable={isUsable}
  defaultEntry={{ label: 'Use install default', sub: 'Follow the install-wide Active voice' }}
  emptyLabel="Configure a voice model →"
  onEmpty={gotoModels}
  onChoose={choose}
  onDefault={useDefault}
  onManage={gotoModels}
  manageLabel="Manage voice models…"
/>
