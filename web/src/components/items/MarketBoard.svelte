<script>
  // "The Exchange" — editorial broadsheet rendering of a MarketBoard A2UI surface.
  // Shares the day/night editorial language with NewsWire/WeatherCard
  // (tokens/editorial.css, theme-aware via [data-theme]). First quote = the lead;
  // the rest form a ruled movers table. Vermillion = down, editorial green = up.
  let { data = {} } = $props()

  const quotes = $derived((Array.isArray(data.quotes) ? data.quotes : []).filter(Boolean))
  const lead = $derived(quotes[0] || null)
  const rest = $derived(quotes.slice(1))
  const title = $derived(data.title || 'Markets')

  // Market status is shown ONLY when the tool could prove it (all exchanges agree).
  const STATUS = { open: 'Market open', closed: 'Market closed', pre: 'Pre-market', after: 'After hours' }
  const statusLabel = $derived(STATUS[data.status] || '')

  const edition = $derived(
    new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  )
  const asOf = $derived.by(() => {
    if (!data.asOf) return ''
    const d = new Date(data.asOf)
    return isNaN(d) ? '' : d.toLocaleTimeString('en-AU', { hour: 'numeric', minute: '2-digit' })
  })

  const up = (q) => Number(q?.changePercent ?? q?.change ?? 0) >= 0
  const arrow = (q) => (up(q) ? '▲' : '▼')
  const sign = (n) => (Number(n) >= 0 ? '+' : '')
  const fmt = (n) =>
    typeof n === 'number'
      ? n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : (n ?? '')
  const pct = (q) => `${sign(q.changePercent)}${Number(q.changePercent).toFixed(2)}%`

  // spark is a normalised 0..100 series; map to an SVG line + area within w×h.
  function sparkLine(spark, w, h) {
    const vs = (Array.isArray(spark) ? spark : []).map(Number)
    if (vs.length < 2) return null
    const pad = 3
    const x = (i) => pad + (i / (vs.length - 1)) * (w - pad * 2)
    const y = (v) => pad + (1 - Math.max(0, Math.min(100, v)) / 100) * (h - pad * 2)
    const pts = vs.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    return {
      line: `M${pts.join(' L')}`,
      area: `M${pts.join(' L')} L${x(vs.length - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z`,
      endX: x(vs.length - 1).toFixed(1),
      endY: y(vs[vs.length - 1]).toFixed(1),
    }
  }

  const leadSpark = $derived(lead ? sparkLine(lead.spark, 300, 120) : null)
  const leadRange = $derived.by(() => {
    if (!lead || lead.dayLow == null || lead.dayHigh == null) return null
    const span = lead.dayHigh - lead.dayLow || 1
    return { pos: Math.max(0, Math.min(100, ((lead.price - lead.dayLow) / span) * 100)) }
  })
</script>

<div class="bs bs--markets">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · Markets</div>
      <h1>{title}</h1>
    </div>
    <div class="bs-edition">
      <div>{edition}</div>
      <div>
        {#if statusLabel}<b class:open={data.status === 'open'}>{statusLabel}</b>{/if}{#if statusLabel && data.currency} · {/if}{#if data.currency}{data.currency}{/if}
      </div>
    </div>
  </header>

  {#if quotes.length}
    <div class="bs-ticker">
      <div class="bs-tag"><span class="bs-dot"></span> The Tape</div>
      <div class="bs-viewport">
        <div class="bs-track">
          {#each [...quotes, ...quotes] as q}
            <span class="q"><b>{q.symbol}</b> {fmt(q.price)} <span class="pc {up(q) ? 'up' : 'down'}">{arrow(q)} {pct(q)}</span></span>
          {/each}
        </div>
      </div>
    </div>
  {/if}

  <div class="bs-body">
    {#if lead}
      <article class="lead" class:nochart={!leadSpark && !leadRange}>
        <div class="lead-text">
          <span class="cat">{rest.length ? 'Lead' : 'Quote'}</span>
          <div class="sym">{lead.symbol}</div>
          <div class="name">{lead.name}{#if lead.exchange} · {lead.exchange}{/if}</div>
          <div class="price-row">
            <span class="price">{fmt(lead.price)}</span>
            <span class="delta {up(lead) ? 'up' : 'down'}">
              <span class="arrow">{arrow(lead)}</span>
              {#if lead.change != null}{sign(lead.change)}{fmt(lead.change)} {/if}({sign(lead.changePercent)}{Number(lead.changePercent).toFixed(2)}%)
            </span>
            {#if lead.currency}<span class="cur">{lead.currency}</span>{/if}
          </div>
          {#if lead.note}<div class="note"><b>What's moving it</b>{lead.note}</div>{/if}
        </div>

        {#if leadSpark || leadRange}
          <div class="lead-chart">
            {#if leadSpark}
              <svg class="spark-lg {up(lead) ? 'up' : 'down'}" viewBox="0 0 300 120" preserveAspectRatio="none">
                <path d={leadSpark.area} class="area" />
                <path d={leadSpark.line} class="ln" />
                <circle cx={leadSpark.endX} cy={leadSpark.endY} r="3" class="end" />
              </svg>
            {/if}
            {#if leadRange}
              <div class="range">
                <div class="range-bar">
                  <div class="range-fill" style="width:{leadRange.pos}%"></div>
                  <div class="range-mark {up(lead) ? 'up' : 'down'}" style="left:{leadRange.pos}%"></div>
                </div>
                <div class="range-ends"><span>L {fmt(lead.dayLow)}</span><span>Day range</span><span>H {fmt(lead.dayHigh)}</span></div>
              </div>
            {/if}
          </div>
        {/if}
      </article>
    {/if}

    {#if rest.length}
      <div class="more-label">Movers</div>
      <div class="list">
        {#each rest as q, i}
          {@const s = sparkLine(q.spark, 78, 30)}
          <div class="row">
            <div class="num">{String(i + 2).padStart(2, '0')}</div>
            <div class="id"><div class="s">{q.symbol}</div><div class="n">{q.name}</div></div>
            <div class="px">{fmt(q.price)}{#if q.currency} <i>{q.currency}</i>{/if}</div>
            <div class="ch {up(q) ? 'up' : 'down'}"><span class="arrow">{arrow(q)}</span><span>{pct(q)}</span></div>
            {#if s}
              <svg class="spark-sm {up(q) ? 'up' : 'down'}" viewBox="0 0 78 30" preserveAspectRatio="none">
                <path d={s.line} class="ln" />
                <circle cx={s.endX} cy={s.endY} r="2.2" class="end" />
              </svg>
            {:else}<span class="spark-sm"></span>{/if}
          </div>
        {/each}
      </div>
    {/if}

    <div class="bs-foot">
      <div class="bs-src">Source: <span>{data.source || 'market data'}</span></div>
      <div class="bs-upd">
        <span class="bs-dot"></span>{#if statusLabel}{statusLabel}{/if}{#if statusLabel && asOf} · {/if}{#if asOf}as of {asOf}{/if}{#if !statusLabel && !asOf}updated just now{/if}
      </div>
    </div>
  </div>
</div>

<style>
  /* Shell (container, masthead, ticker, footer) is shared in broadsheet.css
     (.bs/.bs-*, with .bs--markets for the gains-green live dot). Only
     markets-specific styles live here. */
  .up { color: var(--up-d); } .down { color: var(--accent-d); }

  /* tape item */
  .q { display: inline-flex; align-items: center; gap: 8px; padding: 6px 16px; font-family: var(--code); font-size: 12px; color: var(--ink-2); }
  .q b { font-weight: 700; color: var(--ink); }
  .q .pc { font-weight: 600; }

  .lead { display: grid; grid-template-columns: 1fr 1.05fr; gap: 22px; padding-bottom: 16px; border-bottom: 2px solid var(--ink); align-items: center; }
  .lead.nochart { grid-template-columns: 1fr; }
  .cat { font-family: var(--code); font-size: 9.5px; font-weight: 700; letter-spacing: .2em; text-transform: uppercase; color: var(--accent-d); display: inline-flex; align-items: center; gap: 7px; }
  .cat::before { content: ""; width: 16px; height: 2px; background: var(--accent); }
  .sym { margin: 9px 0 0; font-family: var(--serif); font-weight: 600; font-size: clamp(26px, 5vw, 38px); line-height: 1; letter-spacing: -.015em; font-variation-settings: "opsz" 110; }
  .name { margin: 4px 0 0; font-size: 12.5px; color: var(--ink-2); }
  .price-row { display: flex; align-items: baseline; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
  .price { font-family: var(--code); font-weight: 700; font-size: 30px; letter-spacing: -.02em; line-height: 1; color: var(--ink); }
  .delta { font-family: var(--code); font-size: 14px; font-weight: 600; display: inline-flex; align-items: baseline; gap: 6px; }
  .delta .arrow { font-size: 12px; }
  .cur { font-family: var(--code); font-size: 10px; color: var(--ink-3); letter-spacing: .08em; }
  .note { margin-top: 14px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .note b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }

  .lead-chart { align-self: stretch; display: flex; flex-direction: column; justify-content: center; gap: 12px; }
  .spark-lg { width: 100%; height: 120px; display: block; }
  .ln { fill: none; stroke: currentColor; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .area { fill: currentColor; opacity: .14; stroke: none; }
  .end { fill: currentColor; }
  .range { font-family: var(--code); font-size: 10px; color: var(--ink-3); }
  .range-bar { position: relative; height: 4px; margin: 7px 0 5px; background: var(--rule); border-radius: 2px; }
  .range-fill { position: absolute; top: 0; bottom: 0; left: 0; background: var(--ink-3); opacity: .35; border-radius: 2px; }
  .range-mark { position: absolute; top: 50%; width: 9px; height: 9px; border-radius: 50%; transform: translate(-50%, -50%); border: 2px solid var(--paper); background: currentColor; }
  .range-ends { display: flex; justify-content: space-between; }

  .more-label { margin: 15px 0 0; font-family: var(--code); font-size: 9.5px; font-weight: 700; letter-spacing: .22em; text-transform: uppercase; color: var(--ink-3); }
  .row { display: grid; grid-template-columns: 26px 1fr 110px 92px 78px; gap: 13px; align-items: center; padding: 11px 0; border-top: 1px solid var(--rule); }
  .row:first-child { border-top: 1px solid var(--rule-2); }
  .num { font-family: var(--code); font-size: 11.5px; font-weight: 700; color: var(--ink-3); }
  .id .s { font-family: var(--serif); font-weight: 600; font-size: 16px; line-height: 1.1; }
  .id .n { font-family: var(--ui); font-size: 11px; color: var(--ink-3); margin-top: 1px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .px { font-family: var(--code); font-size: 14px; font-weight: 600; text-align: right; color: var(--ink); }
  .px i { font-style: normal; font-size: 9px; color: var(--ink-3); letter-spacing: .06em; }
  .ch { font-family: var(--code); font-size: 12px; font-weight: 600; text-align: right; display: inline-flex; gap: 5px; justify-content: flex-end; align-items: baseline; }
  .ch .arrow { font-size: 10px; }
  .spark-sm { width: 78px; height: 30px; display: block; }

  @media (max-width: 640px) {
    .lead { grid-template-columns: 1fr; }
    .lead-chart { order: -1; }
    .row { grid-template-columns: 22px 1fr 92px 78px; }
    .spark-sm { display: none; }
  }
</style>
