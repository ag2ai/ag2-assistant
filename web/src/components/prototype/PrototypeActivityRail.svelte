<script lang="ts">
  import Icon from '../Icon.svelte'
  import logo from '../../assets/ag2.svg'
  import logoWhite from '../../assets/ag2-white.svg'
  import { sidebarVariant, sidebarSection, sidebarPanelOpen } from './sidebarPrototype.ts'
  import type { Tab } from '../../lib/route.ts'

  let { profileName, profileInitial, onProfile, onSettings, onSection }: {
    profileName: string; profileInitial: string; onProfile: () => void; onSettings: () => void; onSection: (tab: Tab) => void;
  } = $props()
  const sections: { tab: Tab; label: string; icon: string }[] = [
    { tab: 'chats', label: 'Chats', icon: 'message' },
    { tab: 'tasks', label: 'Tasks', icon: 'list' },
    { tab: 'files', label: 'Files', icon: 'folder' },
    { tab: 'screens', label: 'Screens', icon: 'screen' },
  ]
</script>

<nav class="activity-rail" class:compact={$sidebarVariant === 'B'} class:explorer={$sidebarVariant === 'C'} aria-label="Sections">
  {#if $sidebarVariant === 'B'}
    <button class="avatar top" title={profileName} aria-label="Switch profile" onclick={onProfile}>{profileInitial}</button>
  {:else}
    <div class="rail-brand"><img class="on-light" src={logo} alt="AG2" /><img class="on-dark" src={logoWhite} alt="AG2" /></div>
  {/if}
  <div class="sections">
    {#each sections as section}
      <button class="activity" class:active={$sidebarSection === section.tab && $sidebarPanelOpen}
        aria-label={section.label} aria-pressed={$sidebarSection === section.tab && $sidebarPanelOpen}
        data-label={section.label} onclick={() => onSection(section.tab)}>
        {#if section.icon === 'screen'}
          <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="3" y="3" width="18" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></svg>
        {:else}<Icon name={section.icon} size={21} strokeWidth={1.7} />{/if}
      </button>
    {/each}
  </div>
  <div class="rail-bottom">
    <button class="activity" aria-label="Settings" data-label="Settings" onclick={onSettings}><Icon name="settings" size={20} strokeWidth={1.7} /></button>
    {#if $sidebarVariant !== 'B'}<button class="avatar" title={profileName} aria-label="Switch profile" onclick={onProfile}>{profileInitial}</button>{/if}
  </div>
</nav>

<style>
  .activity-rail { width: 52px; flex: none; height: 100%; display: flex; flex-direction: column; align-items: center; background: var(--surface-sunk); border-right: 1px solid var(--line); padding: 12px 0; gap: 14px; }
  .rail-brand { height: 34px; display: grid; place-items: center; }
  .rail-brand img { width: 25px; height: 25px; }
  .on-dark { display: none; }
  :global([data-theme="dark"]) .on-light { display: none; }
  :global([data-theme="dark"]) .on-dark { display: block; }
  .sections { display: flex; flex-direction: column; gap: 7px; width: 100%; }
  .activity { position: relative; width: 100%; height: 44px; display: grid; place-items: center; border: none; background: none; color: var(--text-muted); cursor: pointer; }
  .activity:hover, .activity:focus-visible { color: var(--text); background: var(--surface-hover); }
  .activity.active { color: var(--accent); }
  .activity.active::before { content: ''; position: absolute; left: 0; top: 9px; bottom: 9px; width: 3px; background: var(--accent); border-radius: 0 2px 2px 0; }
  .activity::after { content: attr(data-label); position: absolute; z-index: 30; left: 47px; top: 10px; padding: 5px 9px; border: 1px solid var(--line); background: var(--surface-elevated); color: var(--text); border-radius: 5px; font-size: 11px; white-space: nowrap; box-shadow: var(--shadow-sm); opacity: 0; pointer-events: none; }
  .activity:hover::after { opacity: 1; }
  .rail-bottom { margin-top: auto; width: 100%; display: flex; flex-direction: column; align-items: center; gap: 15px; }
  .avatar { width: 28px; height: 28px; padding: 0; display: grid; place-items: center; color: var(--accent); border: 1px solid var(--accent); background: var(--accent-soft); border-radius: 50%; font: 600 12px var(--font-sans); cursor: pointer; }
  .avatar:hover { box-shadow: 0 0 0 3px var(--accent-soft); }
  .compact { background: var(--panel); gap: 26px; padding-top: 19px; }
  .compact .activity { width: 38px; height: 38px; margin: 0 auto; border-radius: 10px; }
  .compact .activity.active { background: var(--accent-soft); }
  .compact .activity.active::before { display: none; }
  .explorer .sections { margin: auto 0; gap: 12px; }
  .explorer .rail-bottom { margin-top: 0; }
</style>
