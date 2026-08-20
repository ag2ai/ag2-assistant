<script lang="ts">
  // Theme (light/dark/auto) controls. Shared by Settings and the onboarding
  // "Personalize" step so the two stay in sync — both drive the same shared theme switcher (palette.ts)
  // theme switcher (persisted to localStorage + applied on <html>).
  //
  // §5.3: the accent *palette* is no longer set here. Palette is the profile's
  // identity colour, chosen at profile creation/edit and applied from the
  // registry on boot (App.svelte) — one control, not two competing ones. This
  // component now owns only the global day/night preference.
  import { getTheme, setTheme, type ThemeMode } from '../design/palette.ts'
  import { m } from '../paraglide/messages.js'
  import Icon from './Icon.svelte'

  let theme: ThemeMode = $state(getTheme())
  const THEMES: { id: ThemeMode; label: string; icon: string }[] = [
    { id: 'light', label: m.theme_light(), icon: 'sun' },
    { id: 'dark', label: m.theme_dark(), icon: 'moon' },
    { id: 'auto', label: m.theme_auto(), icon: 'contrast' },
  ]
  function pickTheme(id: ThemeMode) { theme = id; setTheme(id) }
  // Reflect changes made elsewhere (e.g. the header theme toggle).
  $effect(() => {
    const onT = (e: DocumentEventMap['ag2-theme-change']) => { theme = e.detail.theme }
    document.addEventListener('ag2-theme-change', onT)
    return () => document.removeEventListener('ag2-theme-change', onT)
  })
</script>

<div class="appearance">
  <div class="ap-label">{m.appearance_theme()}</div>
  <div class="ap-themes">
    {#each THEMES as t}
      <button class="ap-theme" class:on={theme === t.id} onclick={() => pickTheme(t.id)}>
        <Icon name={t.icon} size={15} /> {t.label}
      </button>
    {/each}
  </div>
</div>

<style>
  .appearance { display: flex; flex-direction: column; gap: 8px; }
  .ap-label { font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-eyebrow); color: var(--text-muted); }
  .ap-themes { display: flex; gap: 8px; }
  .ap-theme {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    border: 1px solid var(--line); background: var(--surface); color: var(--text-muted);
    border-radius: var(--radius-sm); padding: 7px 14px; font: inherit; font-size: var(--text-sm);
    font-weight: var(--fw-medium);
    transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
  }
  .ap-theme:hover { border-color: var(--accent); color: var(--accent); }
  .ap-theme.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
</style>
