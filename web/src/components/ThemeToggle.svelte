<script lang="ts">
  // Compact theme cycle: light → dark → auto. Reads/writes the shared theme switcher (palette.ts)
  // switcher (persists to localStorage + applies [data-theme] on <html>).
  import { getTheme, setTheme, type ThemeMode } from '../design/palette.ts'
  import Icon from './Icon.svelte'

  let mode: ThemeMode = $state(getTheme())
  const ICONS: Record<ThemeMode, string> = { light: 'sun', dark: 'moon', auto: 'contrast' }
  const ORDER: ThemeMode[] = ['light', 'dark', 'auto']
  function cycle() {
    mode = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length]
    setTheme(mode)
  }
  // Stay in sync when the theme is changed elsewhere (e.g. Settings / onboarding).
  $effect(() => {
    const on = (e: DocumentEventMap['ag2-theme-change']) => { mode = e.detail.theme }
    document.addEventListener('ag2-theme-change', on)
    return () => document.removeEventListener('ag2-theme-change', on)
  })
</script>

<button class="themetoggle" onclick={cycle} title={'Theme: ' + mode + ' — click to change'}>
  <Icon name={ICONS[mode]} size={15} /><span>{mode}</span>
</button>

<style>
  .themetoggle {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    border: 1px solid var(--line); background: var(--surface); color: var(--muted);
    border-radius: var(--radius-sm); padding: 5px 10px;
    font-family: var(--font-sans); font-size: var(--text-xs);
    font-weight: var(--fw-semibold); text-transform: capitalize;
    transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
  }
  .themetoggle:hover { border-color: var(--accent); color: var(--accent); }
</style>
