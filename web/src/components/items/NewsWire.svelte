<script>
  // "The Wire" — editorial broadsheet rendering of a NewsDigest A2UI surface.
  // Self-contained light/paper aesthetic (Fraunces serif + JetBrains Mono wire),
  // scoped so it doesn't inherit the chat theme. First story = lead; rest = list.
  let { data = {} } = $props()

  const topic = $derived(data.topic || 'Latest news')
  const stories = $derived((Array.isArray(data.stories) ? data.stories : []).filter(Boolean))
  const lead = $derived(stories[0] || null)
  const rest = $derived(stories.slice(1))
  const sources = $derived([...new Set(stories.map((s) => s && s.source).filter(Boolean))])
  const edition = $derived(
    new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  )

  // back-compat: old surfaces stored "Source · 2h ago" in `meta`
  function srcOf(s) { return s.source || (s.meta ? String(s.meta).split('·')[0].trim() : '') }
  function timeOf(s) { return s.published || (s.meta && String(s.meta).includes('·') ? String(s.meta).split('·')[1].trim() : '') }

  let open = $state({})
  const toggle = (i) => (open = { ...open, [i]: !open[i] })
</script>

<div class="wire">
  <header class="masthead">
    <div class="mast-l">
      <div class="kicker">A2UI · News Digest</div>
      <h1>{topic}</h1>
    </div>
    <div class="edition">
      <div>{edition}</div>
      <div><b>{stories.length} {stories.length === 1 ? 'story' : 'stories'}</b></div>
    </div>
  </header>

  {#if stories.length}
    <div class="ticker">
      <div class="tag"><span class="dot"></span> Live Wire</div>
      <div class="viewport">
        <div class="track">
          {#each [...stories, ...stories] as s}
            <span class="h"><b>{srcOf(s)}</b> {s.title}</span>
          {/each}
        </div>
      </div>
    </div>
  {/if}

  <div class="body">
    {#if lead}
      <article class="lead" class:nofig={!lead.image}>
        <div class="lead-text">
          {#if lead.category}<span class="cat">{lead.category}</span>{/if}
          <h2>
            {#if lead.url}<a href={lead.url} target="_blank" rel="noopener noreferrer">{lead.title}</a>{:else}{lead.title}{/if}
          </h2>
          {#if lead.summary}<p class="deck">{lead.summary}</p>{/if}
          {#if lead.why}<div class="why"><b>Why it matters</b>{lead.why}</div>{/if}
          <div class="byline"><b>{srcOf(lead)}</b>{#if timeOf(lead)} · {timeOf(lead)}{/if}</div>
        </div>
        {#if lead.image}
          <figure class="figure">
            <img src={lead.image} alt="" loading="lazy" />
            <figcaption>{srcOf(lead)}</figcaption>
          </figure>
        {/if}
      </article>
    {/if}

    {#if rest.length}
      <div class="more-label">More on this</div>
      <div class="list">
        {#each rest as s, i}
          <article class="item" class:open={open[i]} onclick={() => toggle(i)}>
            <div class="num">{String(i + 2).padStart(2, '0')}</div>
            <div>
              <h3>
                {#if s.url}<a href={s.url} target="_blank" rel="noopener noreferrer" onclick={(e) => e.stopPropagation()}>{s.title}</a>{:else}{s.title}{/if}
              </h3>
              <div class="meta">
                <b>{srcOf(s)}</b>{#if timeOf(s)} · {timeOf(s)}{/if}{#if s.category} · {s.category}{/if}
              </div>
              {#if s.summary}<div class="summary">{s.summary}</div>{/if}
            </div>
            {#if s.image}<img class="thumb" src={s.image} alt="" loading="lazy" />{/if}
            {#if s.summary}<div class="chev">›</div>{/if}
          </article>
        {/each}
      </div>
    {/if}

    {#if sources.length}
      <div class="foot">
        <div class="src">Sources: {#each sources as s}<span>{s}</span> {/each}</div>
        <div class="upd"><span class="dot"></span> Updated just now</div>
      </div>
    {/if}
  </div>
</div>

<style>
  .wire {
    --paper: #f4eee1; --paper-2: #ece3d2;
    --ink: #18140d; --ink-2: #4f4636; --ink-3: #8a7f6b;
    --accent: #c5402a; --accent-d: #972c1a;
    --rule: rgba(24,20,13,.14); --rule-2: rgba(24,20,13,.28);
    --serif: Fraunces, Georgia, serif;
    --ui: 'Hanken Grotesk', system-ui, sans-serif;
    --code: 'JetBrains Mono', ui-monospace, monospace;
    position: relative;
    background: var(--paper);
    color: var(--ink);
    border: 1px solid var(--rule-2);
    border-radius: 6px;
    overflow: hidden;
    isolation: isolate;
    font-family: var(--ui);
  }
  .wire::after {
    content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 5; opacity: .045;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  }

  .masthead { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding: 15px 20px 11px; }
  .kicker { font-family: var(--code); font-size: 10px; letter-spacing: .26em; text-transform: uppercase; color: var(--accent-d); font-weight: 700; }
  .mast-l h1 { margin: 3px 0 0; font-family: var(--serif); font-weight: 900; font-size: clamp(24px, 5vw, 36px); line-height: .96; letter-spacing: -.01em; font-variation-settings: "opsz" 120; }
  .edition { flex: none; text-align: right; font-family: var(--code); font-size: 10px; line-height: 1.7; color: var(--ink-3); letter-spacing: .03em; }
  .edition b { color: var(--ink-2); font-weight: 700; }

  .ticker { display: flex; align-items: stretch; border-top: 1.5px solid var(--ink); border-bottom: 1px solid var(--rule); background: var(--paper-2); }
  .tag { flex: none; display: flex; align-items: center; gap: 7px; padding: 6px 12px; background: var(--ink); color: var(--paper); font-family: var(--code); font-size: 10px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 0 rgba(197,64,42,.6); animation: pulse 1.6s infinite; }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(197,64,42,.55); } 70% { box-shadow: 0 0 0 7px rgba(197,64,42,0); } 100% { box-shadow: 0 0 0 0 rgba(197,64,42,0); } }
  .viewport { overflow: hidden; flex: 1; position: relative; }
  .viewport::after { content: ""; position: absolute; top: 0; right: 0; bottom: 0; width: 42px; background: linear-gradient(90deg, transparent, var(--paper-2)); }
  .track { display: inline-flex; white-space: nowrap; will-change: transform; animation: marquee 48s linear infinite; }
  .ticker:hover .track { animation-play-state: paused; }
  @keyframes marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
  .track .h { display: inline-flex; align-items: center; gap: 11px; padding: 6px 17px; font-size: 12px; color: var(--ink-2); }
  .track .h::before { content: "◆"; color: var(--accent); font-size: 7px; }
  .track .h b { font-weight: 600; color: var(--ink); }

  .body { padding: 16px 20px 18px; }
  .lead { display: grid; grid-template-columns: 1.25fr 1fr; gap: 20px; padding-bottom: 16px; border-bottom: 2px solid var(--ink); }
  .lead.nofig { grid-template-columns: 1fr; }
  .cat { font-family: var(--code); font-size: 9.5px; font-weight: 700; letter-spacing: .2em; text-transform: uppercase; color: var(--accent-d); display: inline-flex; align-items: center; gap: 7px; }
  .cat::before { content: ""; width: 16px; height: 2px; background: var(--accent); }
  .lead h2 { margin: 9px 0 0; font-family: var(--serif); font-weight: 600; font-size: clamp(22px, 4.4vw, 32px); line-height: 1.03; letter-spacing: -.015em; font-variation-settings: "opsz" 100; }
  .lead h2 a { color: inherit; text-decoration: none; }
  .lead h2 a:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 3px; }
  .deck { margin: 10px 0 0; max-width: 48ch; font-size: 13.5px; line-height: 1.5; color: var(--ink-2); }
  .why { margin-top: 12px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .why b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }
  .byline { margin-top: 13px; font-family: var(--code); font-size: 10.5px; color: var(--ink-3); letter-spacing: .02em; }
  .byline b { color: var(--ink-2); font-weight: 700; }
  .figure { position: relative; align-self: stretch; min-height: 190px; border: 1px solid var(--rule-2); overflow: hidden; background: var(--paper-2); }
  .figure img { width: 100%; height: 100%; object-fit: cover; display: block; filter: saturate(.92) contrast(1.02); animation: kenburns 20s ease-in-out infinite alternate; }
  @keyframes kenburns { from { transform: scale(1.06); } to { transform: scale(1.16) translate(-2.5%, -2%); } }
  .figure figcaption { position: absolute; left: 0; bottom: 0; margin: 0; padding: 4px 9px; background: var(--ink); color: var(--paper); font-family: var(--code); font-size: 8.5px; letter-spacing: .1em; text-transform: uppercase; }

  .more-label { margin: 15px 0 0; font-family: var(--code); font-size: 9.5px; font-weight: 700; letter-spacing: .22em; text-transform: uppercase; color: var(--ink-3); }
  .item { display: grid; grid-template-columns: 28px 1fr auto; gap: 13px; align-items: start; padding: 12px 0; border-top: 1px solid var(--rule); cursor: pointer; }
  .item:first-child { border-top: 1px solid var(--rule-2); }
  .num { font-family: var(--code); font-size: 11.5px; font-weight: 700; color: var(--accent); padding-top: 2px; }
  .item h3 { margin: 0; font-family: var(--serif); font-weight: 500; font-size: 16.5px; line-height: 1.18; letter-spacing: -.005em; transition: color .15s; }
  .item h3 a { color: inherit; text-decoration: none; }
  .item h3 a:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 2px; }
  .item:hover h3 { color: var(--accent-d); }
  .meta { margin-top: 4px; font-family: var(--code); font-size: 10px; color: var(--ink-3); letter-spacing: .02em; }
  .meta b { color: var(--ink-2); font-weight: 700; }
  .summary { grid-column: 2 / 3; max-height: 0; overflow: hidden; font-size: 12.5px; line-height: 1.5; color: var(--ink-2); opacity: 0; transition: max-height .32s ease, margin-top .32s ease, opacity .25s; }
  .item.open .summary { max-height: 140px; margin-top: 7px; opacity: 1; }
  .thumb { width: 60px; height: 46px; border: 1px solid var(--rule-2); object-fit: cover; filter: saturate(.9) contrast(1.02); align-self: start; }
  .chev { width: 26px; text-align: center; color: var(--ink-3); font-family: var(--code); font-size: 13px; transition: transform .25s, color .15s; align-self: start; padding-top: 1px; }
  .item.open .chev { transform: rotate(90deg); color: var(--accent); }
  .item:hover .chev { color: var(--accent); }

  .foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 15px; padding-top: 10px; border-top: 1.5px solid var(--ink); font-family: var(--code); font-size: 10px; color: var(--ink-3); letter-spacing: .03em; }
  .src { display: flex; gap: 8px; flex-wrap: wrap; }
  .src span { color: var(--ink-2); }
  .upd { display: inline-flex; align-items: center; gap: 6px; flex: none; }

  @media (max-width: 640px) {
    .lead { grid-template-columns: 1fr; }
    .figure { min-height: 160px; order: -1; }
    .item { grid-template-columns: 24px 1fr; }
    .thumb, .item .chev { display: none; }
  }
</style>
