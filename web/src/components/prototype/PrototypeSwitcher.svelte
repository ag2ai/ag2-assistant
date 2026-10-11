<script lang="ts">
  import Icon from '../Icon.svelte'
  import { cycleSidebarVariant, sidebarVariant, sidebarSection, sidebarPanelOpen, prototypeNames } from './sidebarPrototype.ts'

  function keyboard(event: KeyboardEvent) {
    const target = event.target
    if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable]')) return
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    cycleSidebarVariant(event.key === 'ArrowLeft' ? -1 : 1)
  }
</script>

<svelte:window onkeydown={keyboard} />

{#if import.meta.env.DEV}
  <div class="prototype-switcher" aria-label="Sidebar prototype variants">
    <span class="badge">PROTOTYPE</span>
    <button aria-label="Previous variant" onclick={() => cycleSidebarVariant(-1)}><Icon name="chevron-left" size={17} /></button>
    <div class="variant"><strong>{$sidebarVariant} · {prototypeNames[$sidebarVariant]}</strong><small>{$sidebarSection} · {$sidebarPanelOpen ? 'panel open' : 'panel closed'}</small></div>
    <button aria-label="Next variant" onclick={() => cycleSidebarVariant(1)}><Icon name="chevron-right" size={17} /></button>
  </div>
{/if}

<style>
  .prototype-switcher { position: fixed; z-index: 2000; bottom: 18px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 10px; padding: 8px 12px; color: #f5f6f8; background: #252a34; border: 1px solid #505865; border-radius: 14px; box-shadow: 0 8px 30px #0005; font-family: var(--font-sans); }
  .badge { font: 10px var(--font-mono); letter-spacing: 1px; color: #b6becb; }
  button { display: grid; place-items: center; width: 32px; height: 32px; color: inherit; border: 1px solid #505865; background: #343b47; border-radius: 8px; cursor: pointer; }
  button:hover { background: #4a5362; }
  .variant { min-width: 190px; display: flex; flex-direction: column; gap: 2px; text-align: center; }
  strong { font-size: 12px; font-weight: 500; }
  small { font-size: 10px; color: #b6becb; text-transform: capitalize; }
  @media (max-width: 600px) { .badge { display: none; } .prototype-switcher { bottom: 10px; max-width: calc(100vw - 20px); } .variant { min-width: 160px; } }
</style>
