<script>
  // Accent palette + light/dark/auto controls. Shared by Settings and the
  // onboarding "Personalize" step so the two stay perfectly in sync — both drive
  // the same AG2Palette switcher (persisted to localStorage + applied on <html>).
  import { PALETTES, getPalette, setPalette, getTheme, setTheme } from '../design/palette.js'
  import Icon from './Icon.svelte'

  let palette = $state(getPalette())
  let theme = $state(getTheme())
  const THEMES = [
    { id: 'light', label: 'Light', icon: 'sun' },
    { id: 'dark', label: 'Dark', icon: 'moon' },
    { id: 'auto', label: 'Auto', icon: 'contrast' },
  ]
  function pickPalette(id) { palette = id; setPalette(id) }
  function pickTheme(id) { theme = id; setTheme(id) }
  // Reflect changes made elsewhere (e.g. the header theme toggle).
  $effect(() => {
    const onT = (e) => { theme = e.detail.theme }
    const onP = (e) => { palette = e.detail.palette }
    document.addEventListener('ag2-theme-change', onT)
    document.addEventListener('ag2-palette-change', onP)
    return () => {
      document.removeEventListener('ag2-theme-change', onT)
      document.removeEventListener('ag2-palette-change', onP)
    }
  })
</script>

<div class="appearance">
  <div class="ap-label">Accent</div>
  <div class="ap-swatches">
    {#each PALETTES as p}
      <button class="ap-swatch" class:on={palette === p.id} title={p.label}
              style="--sw:{p.hex}" aria-label={p.label} onclick={() => pickPalette(p.id)}>
        {#if palette === p.id}<Icon name="check" size={14} />{/if}
      </button>
    {/each}
  </div>

  <div class="ap-label">Theme</div>
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
  .ap-swatches { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 4px; }
  .ap-swatch {
    width: 30px; height: 30px; flex: none; border-radius: var(--radius-pill);
    background: var(--sw); border: 2px solid var(--surface);
    box-shadow: 0 0 0 1px var(--line); cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    color: #fff; transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .ap-swatch:hover { transform: translateY(-1px); }
  .ap-swatch.on { box-shadow: 0 0 0 2px var(--sw); }
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
