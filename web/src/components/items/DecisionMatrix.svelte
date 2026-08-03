<script lang="ts">
  // "The Verdict" — editorial broadsheet rendering of a DecisionMatrix A2UI
  // surface. Shares the day/night editorial language with NewsWire/MarketBoard/
  // WeatherCard (broadsheet.css .bs shell). Options are columns, criteria are
  // ruled rows; the recommended option gets the accent column and the verdict
  // reads like a pull quote. No ticker — a decision is a considered piece, not
  // a live feed.
  import { rows, str } from '../../lib/a2ui.ts'
  import type { A2UIData, DecisionCriterion, DecisionOption } from '../../lib/a2ui.ts'

  type Props = { data?: A2UIData }
  let { data = {} }: Props = $props()

  const topic = $derived(str(data.topic) || 'Decision')
  const options = $derived(rows<DecisionOption>(data.options))
  const criteria = $derived(rows<DecisionCriterion>(data.criteria))
  const verdict = $derived(str(data.verdict))
  const recIdx = $derived(options.findIndex((o) => o.name === data.recommended))
  const wins = $derived(options.map((o) => criteria.filter((c) => c.best === o.name).length))

  const edition = $derived(
    new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  )
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · Decision</div>
      <h1>{topic}</h1>
    </div>
    <div class="bs-edition">
      <div>{edition}</div>
      <div><b>{options.length} {options.length === 1 ? 'option' : 'options'}</b></div>
    </div>
  </header>

  <div class="bs-body">
    <div class="matrix" style="grid-template-columns: minmax(96px, 0.9fr) repeat({options.length}, 1fr)">
      <!-- header row: option names (+ tagline/price), recommended flagged -->
      <div class="cell corner"></div>
      {#each options as o, i}
        <div class="cell head" class:rec={i === recIdx}>
          {#if i === recIdx}<span class="pick">The pick</span>{/if}
          <div class="oname">{o.name}</div>
          {#if o.tagline}<div class="otag">{o.tagline}</div>{/if}
          {#if o.price}<div class="oprice">{o.price}</div>{/if}
        </div>
      {/each}

      {#each criteria as c}
        <div class="cell crit">{c.label}</div>
        {#each options as o, i}
          <div class="cell val" class:rec={i === recIdx} class:best={c.best === o.name}>
            {(c.values || [])[i] ?? '—'}
            {#if c.best === o.name}<span class="star" title="Wins this criterion">●</span>{/if}
          </div>
        {/each}
      {/each}

      {#if criteria.some((c) => c.best)}
        <div class="cell crit tally">Criteria won</div>
        {#each wins as won, i}
          <div class="cell val tally" class:rec={i === recIdx}>{won} / {criteria.length}</div>
        {/each}
      {/if}
    </div>

    {#if verdict}
      <div class="verdict"><b>The verdict</b>{verdict}</div>
    {/if}

    <div class="bs-foot">
      <div class="bs-src">Assistant analysis — <span>verify before you buy</span></div>
      <div class="bs-upd">{criteria.length} criteria compared</div>
    </div>
  </div>
</div>

<style>
  /* Shell (container, masthead, footer) is shared in broadsheet.css (.bs/.bs-*).
     Only the matrix-specific styles live here. */
  .matrix { display: grid; border-top: 1.5px solid var(--ink); border-left: 1px solid var(--rule); }
  .cell { padding: 9px 12px; border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); font-size: 12.5px; line-height: 1.4; }
  .corner { background: var(--paper-2); }

  .head { position: relative; background: var(--paper-2); }
  .pick { position: absolute; top: 0; right: 0; padding: 2px 7px; background: var(--accent); color: var(--paper); font-family: var(--code); font-size: 8px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
  .oname { font-family: var(--serif); font-weight: 600; font-size: 15.5px; line-height: 1.15; letter-spacing: -.01em; }
  .otag { margin-top: 3px; font-size: 11px; color: var(--ink-2); }
  .oprice { margin-top: 3px; font-family: var(--code); font-size: 11px; font-weight: 600; color: var(--ink); }

  .crit { font-family: var(--code); font-size: 10px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); display: flex; align-items: center; }
  .val { color: var(--ink); position: relative; padding-right: 22px; }
  .val .star { position: absolute; right: 9px; top: 50%; transform: translateY(-50%); color: var(--accent); font-size: 8px; }
  .val.best { font-weight: 600; }

  /* recommended column: a quiet accent wash down the whole column */
  .rec { box-shadow: inset 3px 0 0 var(--accent); background: color-mix(in srgb, var(--accent) 5%, transparent); }

  .tally { background: var(--paper-2); font-weight: 700; }
  .cell.val.tally { font-family: var(--code); font-size: 12px; }

  .verdict { margin-top: 14px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 13px; line-height: 1.55; color: var(--ink); max-width: 68ch; }
  .verdict b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }

  @media (max-width: 640px) {
    .cell { padding: 8px 8px; font-size: 11.5px; }
    .oname { font-size: 13.5px; }
    .otag { display: none; }
  }
</style>
