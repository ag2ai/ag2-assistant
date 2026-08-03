<script lang="ts">
  import { animations } from '../../store.ts'
  import WeatherGlyphBasic from './WeatherGlyphBasic.svelte'

  type Props = {
    condition?: string
    temperatureText?: string
    flush?: boolean
    zoom?: number
  }
  let { condition = 'cloudy', temperatureText = '', flush = false, zoom = 1 }: Props = $props()

  // Effective tier: 'high' needs WebGPU — browsers without it get 'basic'
  // (animated vector glyphs) rather than a dead gradient. `gpu` is not in lib.dom,
  // so the probe is a key check rather than a property read.
  const webgpu = typeof navigator !== 'undefined' && 'gpu' in navigator && !!navigator.gpu
  const mode = $derived($animations === 'high' && !webgpu ? 'basic' : $animations)

  const EMOJI: Record<string, string | undefined> = {
    sunny: '☀️',
    'partly-cloudy': '⛅',
    cloudy: '☁️',
    foggy: '🌫️',
    rainy: '🌧️',
    thunderstorm: '⛈️',
    snow: '🌨️',
    windy: '💨',
  }
  // HTML temperature tone per condition (dark digits on pale skies, light on dark)
  const LIGHT_TEMP = new Set(['rainy', 'thunderstorm'])

  let canvas: HTMLCanvasElement | undefined = $state()
  let active = $state(false) // WebGPU banner is live

  // Engine lifecycle tracks the canvas: it exists only while mode === 'high', so
  // flipping the Settings tier live disposes/recreates the scene automatically.
  $effect(() => {
    if (!canvas) return
    let cancelled = false
    let handle: { dispose: () => void } | null = null
    ;(async () => {
      try {
        const eng = await import('../../lib/weather/engine.js')
        if (!eng.supportsWebGPU()) return
        handle = await eng.createBanner(canvas, condition, { temperatureText, zoom })
        if (cancelled) { handle.dispose(); handle = null; return }
        active = true
      } catch {
        active = false // fall back to the static gradient
      }
    })()
    return () => { cancelled = true; active = false; if (handle) handle.dispose() }
  })
</script>

<div class="wx-banner wx-{condition}" class:wx-live={active} class:wx-flush={flush}>
  {#if mode === 'high'}
    <canvas bind:this={canvas} aria-hidden="true"></canvas>
  {:else}
    {#if mode === 'basic'}
      <WeatherGlyphBasic {condition} />
    {:else}
      <div class="wx-emoji" aria-hidden="true">{EMOJI[condition] || '☁️'}</div>
    {/if}
    {#if temperatureText}
      <div class="wx-temp" class:wx-temp-light={LIGHT_TEMP.has(condition)}>{temperatureText}</div>
    {/if}
  {/if}
</div>

<style>
  .wx-banner {
    position: relative;
    container-type: size; /* cqh sizes the HTML temp/emoji to the pill height */
    width: 100%;
    aspect-ratio: 16 / 6;
    max-height: 220px;
    border-radius: var(--radius-sm, 8px);
    overflow: hidden;
    margin-bottom: 12px;
    /* fallback gradients per condition (shown until/unless WebGPU canvas is live) */
    background: linear-gradient(180deg, #9fb6cd, #dde7ed);
  }
  .wx-banner canvas { display: block; width: 100%; height: 100%; }

  /* Flush variant: fill the parent pill edge-to-edge (parent owns border+radius). */
  .wx-flush {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    max-height: none;
    aspect-ratio: auto;
    border-radius: 0;
    margin-bottom: 0;
  }

  /* HTML temperature for the off/basic tiers — mirrors the 3D layout: half the
     panel height (digit cap-height ≈ 0.72em → 68cqh ≈ 50cqh of digits),
     vertically centred, in the left region */
  .wx-temp {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 44%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 68cqh;
    font-weight: 750;
    letter-spacing: -0.03em;
    color: #212b38;
    line-height: 1;
    user-select: none;
  }
  .wx-temp-light { color: #e9eef6; }

  .wx-emoji {
    position: absolute;
    right: 5%;
    top: 50%;
    transform: translateY(-50%);
    font-size: 78cqh;
    line-height: 1;
    user-select: none;
  }

  .wx-sunny { background: radial-gradient(120% 90% at 30% 30%, #ffd27a, #ff9d3c 55%, #2c1008); }
  .wx-partly-cloudy { background: linear-gradient(180deg, #82b0cf, #e1edf2); }
  .wx-cloudy { background: linear-gradient(180deg, #6f9ec4, #dfeaf0); }
  .wx-foggy { background: linear-gradient(180deg, #b7c4c6, #dbe0de); }
  .wx-rainy { background: linear-gradient(180deg, #2f3743, #5d6772); }
  .wx-thunderstorm { background: linear-gradient(180deg, #141a22, #333d48); }
  .wx-snow { background: linear-gradient(180deg, #8c9eb0, #c6d1d8); }
  .wx-windy { background: linear-gradient(180deg, #6ba2d4, #cfe4f4); }
</style>
