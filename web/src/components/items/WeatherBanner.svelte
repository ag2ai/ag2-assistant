<script>
  import { onMount } from 'svelte'

  let { condition = 'cloudy', temperatureText = '' } = $props()

  let canvas
  let active = $state(false) // WebGPU banner is live

  onMount(() => {
    let cancelled = false
    let handle = null
    ;(async () => {
      try {
        const eng = await import('../../lib/weather/engine.js')
        if (!eng.supportsWebGPU()) return
        handle = await eng.createBanner(canvas, condition, { temperatureText })
        if (cancelled) { handle.dispose(); handle = null; return }
        active = true
      } catch (e) {
        active = false // fall back to the static gradient
      }
    })()
    return () => { cancelled = true; if (handle) handle.dispose() }
  })
</script>

<div class="wx-banner wx-{condition}" class:wx-live={active}>
  <canvas bind:this={canvas} aria-hidden="true"></canvas>
</div>

<style>
  .wx-banner {
    position: relative;
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

  .wx-sunny { background: radial-gradient(120% 90% at 30% 30%, #ffd27a, #ff9d3c 55%, #2c1008); }
  .wx-partly-cloudy { background: linear-gradient(180deg, #82b0cf, #e1edf2); }
  .wx-cloudy { background: linear-gradient(180deg, #6f9ec4, #dfeaf0); }
  .wx-foggy { background: linear-gradient(180deg, #b7c4c6, #dbe0de); }
  .wx-rainy { background: linear-gradient(180deg, #2f3743, #5d6772); }
  .wx-thunderstorm { background: linear-gradient(180deg, #141a22, #333d48); }
  .wx-snow { background: linear-gradient(180deg, #8c9eb0, #c6d1d8); }
  .wx-windy { background: linear-gradient(180deg, #6ba2d4, #cfe4f4); }
</style>
