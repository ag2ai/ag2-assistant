<script lang="ts">
  import { route, switchTab, openOverlay } from '../router.ts'
  import type { Tab } from '../lib/route.ts'
  import Icon from './Icon.svelte'
  import { tasks } from '../store.ts'
  import { badgeText, unreadRuns } from '../lib/unread.ts'
  import ag2Logo from '../assets/ag2.svg'
  import ag2LogoWhite from '../assets/ag2-white.svg'

  const sections: { tab: Tab; label: string; icon: string }[] = [
    { tab: 'chats', label: 'Chats', icon: 'message' },
    { tab: 'tasks', label: 'Tasks', icon: 'clock' },
    { tab: 'files', label: 'Files', icon: 'folder' },
    { tab: 'screens', label: 'Screens', icon: 'file-text' },
  ]
  const unread = $derived<Partial<Record<Tab, number>>>({ tasks: unreadRuns($tasks) })
  const label = (section: { tab: Tab; label: string }) =>
    unread[section.tab] ? `${section.label}, ${unread[section.tab]} unread` : section.label
</script>

<nav class="activity-rail" aria-label="Sections">
  <div class="rail-brand">
    <img class="brandlogo on-light" src={ag2Logo} alt="AG2" />
    <img class="brandlogo on-dark" src={ag2LogoWhite} alt="AG2" />
  </div>
  {#each sections as section}
    <button class="activity" class:active={$route.tab === section.tab}
      aria-label={label(section)} aria-pressed={$route.tab === section.tab}
      data-label={label(section)} onclick={() => switchTab(section.tab)}>
      <Icon name={section.icon} size={21} strokeWidth={1.7} />
      {#if unread[section.tab]}<span class="badge" aria-hidden="true">{badgeText(unread[section.tab] ?? 0)}</span>{/if}
    </button>
  {/each}
  <div class="rail-bottom">
    <button class="activity" aria-label="Settings" data-label="Settings" aria-haspopup="dialog"
      aria-expanded={$route.overlay === 'settings'} onclick={() => openOverlay('settings', 'general')}>
      <Icon name="settings" size={20} strokeWidth={1.7} />
    </button>
  </div>
</nav>

<style>
  .activity-rail { display: flex; flex-direction: column; align-items: center; gap: 7px; padding: 12px 0; background: var(--panel); border-right: 1px solid var(--line); }
  .rail-brand { height: 30px; display: grid; place-items: center; margin-bottom: 12px; }
  .rail-bottom { margin-top: auto; padding-top: 14px; }
  .activity { position: relative; display: grid; place-items: center; width: 38px; height: 38px; padding: 0; flex: none; border: none; border-radius: 10px; background: none; color: var(--text-muted); cursor: pointer; }
  .activity:hover { color: var(--text); background: var(--surface-hover); }
  .activity.active { color: var(--accent); background: var(--accent-soft); }
  .activity:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .activity::after { content: attr(data-label); position: absolute; z-index: 30; left: 40px; top: 6px; padding: 5px 9px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface-elevated); color: var(--text); font-size: 11px; white-space: nowrap; box-shadow: var(--shadow-sm); opacity: 0; pointer-events: none; }
  .activity:hover::after, .activity:focus-visible::after { opacity: 1; }
  .badge { position: absolute; top: 1px; right: 0; min-width: 16px; height: 16px; padding: 0 4px; box-sizing: border-box; border: 2px solid var(--panel); border-radius: 999px; background: var(--accent); color: var(--text-on-accent); font-size: 10px; font-weight: 600; line-height: 12px; text-align: center; pointer-events: none; }
</style>
