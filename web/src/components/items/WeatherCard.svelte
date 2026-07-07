<script>
  // Editorial broadsheet rendering of a WeatherPanel A2UI surface.
  // Shares the "day/night edition" paper language with NewsWire/MarketBoard
  // (tokens/editorial.css, theme-aware via [data-theme]). The WebGPU
  // WeatherBanner is the hero band; rows become a ruled metric grid.
  import WeatherBanner from './WeatherBanner.svelte'

  let { data = {} } = $props()

  const WEATHER_CONDITIONS = ['sunny', 'partly-cloudy', 'cloudy', 'foggy', 'rainy', 'thunderstorm', 'snow', 'windy']

  const condition = $derived(
    WEATHER_CONDITIONS.includes(String(data.condition || '').toLowerCase())
      ? String(data.condition).toLowerCase()
      : 'cloudy'
  )
  const rows = $derived((Array.isArray(data.rows) ? data.rows : []).filter(Boolean))
  const location = $derived(data.location || 'Forecast')
  const summary = $derived(data.summary || '')
  const conditionLabel = $derived(
    condition.split('-').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
  )
  const tempText = $derived.by(() => {
    const r = rows.find((x) => /temp/i.test(x?.label || ''))
    const m = String(r?.value || '').match(/-?\d+°?/)
    return m ? m[0] : ''
  })
  const edition = $derived(
    new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  )
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · Weather</div>
      <h1>{location}</h1>
    </div>
    <div class="bs-edition">
      <div>{edition}</div>
      <div><b>{conditionLabel}</b></div>
    </div>
  </header>

  <div class="hero">
    {#key condition}
      <WeatherBanner {condition} temperatureText={tempText} zoom={1.3} flush />
    {/key}
  </div>

  <div class="bs-body">
    {#if summary}<p class="deck">{summary}</p>{/if}

    {#if rows.length}
      <div class="grid">
        {#each rows as r}
          <div class="cell">
            <div class="label">{r.label}</div>
            <div class="val">{r.value}</div>
          </div>
        {/each}
      </div>
    {/if}

    <div class="bs-foot">
      <div class="bs-src">Source: <span>wttr.in</span></div>
      <div class="bs-upd"><span class="bs-dot"></span> Updated just now</div>
    </div>
  </div>
</div>

<style>
  /* Shell (container, masthead, footer) is shared in broadsheet.css (.bs/.bs-*).
     Only weather-specific body styles live here. */
  /* Bold pill: temperature (in-scene) reads left, the zoomed weather glyph rides
     right, cropped by the rounded border — echoes the reference weather chips. */
  .hero {
    position: relative;
    aspect-ratio: 16 / 5;
    max-height: 220px;
    /* aspect-ratio + max-height can transfer a narrower width to the box; auto
       inline margins keep the pill horizontally centred when that happens */
    margin: 4px auto 14px;
    border: 2.5px solid var(--ink);
    border-radius: 16px;
    overflow: hidden;
    z-index: 1;
  }
  .deck { margin: 0 0 14px; max-width: 60ch; font-size: 13.5px; line-height: 1.5; color: var(--ink-2); }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); border-top: 1.5px solid var(--ink); border-left: 1px solid var(--rule); }
  .cell { padding: 9px 14px 11px; border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
  .label { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--ink-3); }
  .val { font-family: var(--code); font-size: 16px; font-weight: 600; color: var(--ink); margin-top: 4px; letter-spacing: -.01em; }
</style>
