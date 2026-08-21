<script lang="ts">
  // Profile editor (ADR 0015, redesign §2 + §9): the second level of Settings → Profiles.
  // Reached by clicking a card in the catalogue. A profile is now a first-class entity with
  // its own header (accent, name, Active badge, back to the catalogue) and a horizontal tab
  // bar — General, Focus areas, Model, Folders, Skills, Memory — each a surface scoped to
  // the ACTIVE profile (the catalogue switches to the clicked profile before opening this).
  // Channel exposure is not here: it is set per connection, in Settings → Integrations.
  import { profiles } from '../../store.ts'
  import { getActiveProfileId } from '../../lib/profile.ts'
  import { m } from '../../paraglide/messages.js'
  import Icon from '../Icon.svelte'
  import ProfileGeneralSection from './ProfileGeneralSection.svelte'
  import FocusAreasSection from './FocusAreasSection.svelte'
  import ProfileModelSwitchers from './ProfileModelSwitchers.svelte'
  import FoldersSection from './FoldersSection.svelte'
  import ProfileSkillsSection from './ProfileSkillsSection.svelte'
  import ProfileMemorySection from './ProfileMemorySection.svelte'

  type Props = { onBack: () => void }
  let { onBack }: Props = $props()

  const list = $derived($profiles.list || [])
  const activeId = $derived($profiles.activeId || getActiveProfileId())
  const active = $derived(list.find((p) => p.id === activeId) || null)

  const TABS = [
    { id: 'general', label: m.profile_tab_general(), comp: ProfileGeneralSection },
    { id: 'focus', label: m.profile_tab_focus(), comp: FocusAreasSection },
    { id: 'model', label: m.profile_tab_model(), comp: ProfileModelSwitchers },
    { id: 'folders', label: m.profile_tab_folders(), comp: FoldersSection },
    { id: 'skills', label: m.profile_tab_skills(), comp: ProfileSkillsSection },
    { id: 'memory', label: m.profile_tab_memory(), comp: ProfileMemorySection },
  ]
  let tab = $state('general')
  const Panel = $derived((TABS.find((t) => t.id === tab) || TABS[0]).comp)
</script>

<div class="peditor">
  <button class="pback" onclick={onBack}><Icon name="chevron-left" size={15} /> {m.settings_page_profiles()}</button>

  <header class="phead" style="--dot:{active?.accent || 'var(--accent)'}">
    <span class="pheaddot"></span>
    <div class="pheadmeta">
      <div class="pheadname">
        {active?.name || m.profile_fallback_name()}
        <span class="pbadge">{m.profile_badge_active()}</span>
      </div>
      {#if active?.workspace}<div class="pheadsub" title={active.workspace}>{active.workspace}</div>{/if}
    </div>
  </header>

  <nav class="ptabs">
    {#each TABS as t}
      <button class="ptab" class:on={tab === t.id} onclick={() => (tab = t.id)}>{t.label}</button>
    {/each}
  </nav>

  <div class="ppanel">
    <Panel />
  </div>
</div>

<style>
  .peditor { display: flex; flex-direction: column; gap: 4px; }

  .pback {
    display: inline-flex; align-items: center; gap: 3px; align-self: flex-start;
    margin: 0 0 8px -4px; padding: 4px 4px;
    background: none; border: none; cursor: pointer; font: inherit;
    font-size: var(--text-xs); font-weight: var(--fw-semibold); color: var(--text-muted);
  }
  .pback:hover { color: var(--accent); }
  .pback:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }

  .phead { display: flex; align-items: center; gap: 12px; padding: 2px 0 14px; }
  .pheaddot { width: 14px; height: 14px; flex: none; border-radius: var(--radius-pill); background: var(--dot); }
  .pheadmeta { min-width: 0; }
  .pheadname { display: flex; align-items: center; gap: 10px; font-size: var(--text-lg, 18px); font-weight: var(--fw-semibold); }
  .pbadge {
    font-size: var(--text-xs); font-weight: var(--fw-semibold); text-transform: uppercase;
    letter-spacing: var(--tracking-eyebrow); color: var(--accent);
    background: var(--accent-soft); border-radius: var(--radius-pill); padding: 1px 7px;
  }
  .pheadsub { font-size: var(--text-xs); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }

  .ptabs {
    display: flex; gap: 2px; flex-wrap: wrap;
    border-bottom: 1px solid var(--line); margin-bottom: 16px;
  }
  .ptab {
    position: relative; padding: 9px 12px; margin-bottom: -1px;
    background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer;
    font: inherit; font-size: var(--text-sm); font-weight: var(--fw-medium); color: var(--text-muted);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .ptab:hover { color: var(--text); }
  .ptab.on { color: var(--accent); border-bottom-color: var(--accent); font-weight: var(--fw-semibold); }
  .ptab:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }

  .ppanel { display: flex; flex-direction: column; gap: 6px; }
</style>
