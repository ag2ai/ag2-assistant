<script lang="ts">
  // "The Wire" — editorial broadsheet rendering of a NewsDigest A2UI surface.
  // Self-contained light/paper aesthetic (Fraunces serif + JetBrains Mono wire),
  // scoped so it doesn't inherit the chat theme. First story = lead; rest = list.
  import { safeUrl } from '../../lib/url.ts'
  import { rows, str } from '../../lib/a2ui.ts'
  import type { A2UIData, NewsStory } from '../../lib/a2ui.ts'
  import { getLocale } from '../../paraglide/runtime.js'
  import { m } from '../../paraglide/messages.js'

  type Props = { data?: A2UIData }
  let { data = {} }: Props = $props()

  const topic = $derived(str(data.topic) || m.a2ui_latest_news())
  const stories = $derived(rows<NewsStory>(data.stories))
  const lead = $derived(stories[0] || null)
  const rest = $derived(stories.slice(1))
  const sources = $derived([...new Set(stories.map((s) => s.source).filter(Boolean))])
  const edition = $derived(
    new Date().toLocaleDateString(getLocale(), { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  )

  // back-compat: old surfaces stored "Source · 2h ago" in `meta`
  function srcOf(s: NewsStory) { return s.source || (s.meta ? String(s.meta).split('·')[0].trim() : '') }
  function timeOf(s: NewsStory) { return s.published || (s.meta && String(s.meta).includes('·') ? String(s.meta).split('·')[1].trim() : '') }

  let open: Record<number, boolean> = $state({})
  const toggle = (i: number) => (open = { ...open, [i]: !open[i] })
  // Enter/Space activate the disclosure like a button; preventDefault stops Space
  // from scrolling the page.
  const onKey = (e: KeyboardEvent, i: number) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggle(i)
    }
  }
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · {m.a2ui_news_digest()}</div>
      <h1>{topic}</h1>
    </div>
    <div class="bs-edition">
      <div>{edition}</div>
      <div><b>{m.a2ui_stories_count({ count: stories.length })}</b></div>
    </div>
  </header>

  {#if stories.length}
    <div class="bs-ticker">
      <div class="bs-tag"><span class="bs-dot"></span> {m.a2ui_live_wire()}</div>
      <div class="bs-viewport">
        <div class="bs-track">
          {#each [...stories, ...stories] as s}
            <span class="h"><b>{srcOf(s)}</b> {s.title}</span>
          {/each}
        </div>
      </div>
    </div>
  {/if}

  <div class="bs-body">
    {#if lead}
      <article class="lead" class:nofig={!lead.image}>
        <div class="lead-text">
          {#if lead.category}<span class="cat">{lead.category}</span>{/if}
          <h2>
            {#if safeUrl(lead.url)}<a href={safeUrl(lead.url)} target="_blank" rel="noopener noreferrer">{lead.title}</a>{:else}{lead.title}{/if}
          </h2>
          {#if lead.summary}<p class="deck">{lead.summary}</p>{/if}
          {#if lead.why}<div class="why"><b>{m.a2ui_why_it_matters()}</b>{lead.why}</div>{/if}
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
      <div class="more-label">{m.a2ui_more_on_this()}</div>
      <div class="list">
        {#snippet row(s: NewsStory, i: number)}
          <div class="num">{String(i + 2).padStart(2, '0')}</div>
          <div>
            <h3>
              {#if safeUrl(s.url)}<a href={safeUrl(s.url)} target="_blank" rel="noopener noreferrer" onclick={(e) => e.stopPropagation()}>{s.title}</a>{:else}{s.title}{/if}
            </h3>
            <div class="meta">
              <b>{srcOf(s)}</b>{#if timeOf(s)} · {timeOf(s)}{/if}{#if s.category} · {s.category}{/if}
            </div>
            {#if s.summary}<div class="summary">{s.summary}</div>{/if}
          </div>
          {#if s.image}<img class="thumb" src={s.image} alt="" loading="lazy" />{/if}
          {#if s.summary}<div class="chev">›</div>{/if}
        {/snippet}
        {#each rest as s, i}
          <!-- A story with a summary is a disclosure: click/Enter/Space toggles it
               open. Stories without one have nothing to reveal, so they render as a
               plain, non-interactive row. -->
          {#if s.summary}
            <div
              class="item"
              class:open={open[i]}
              role="button"
              tabindex="0"
              aria-expanded={open[i] ? true : false}
              onclick={() => toggle(i)}
              onkeydown={(e) => onKey(e, i)}
            >
              {@render row(s, i)}
            </div>
          {:else}
            <div class="item">{@render row(s, i)}</div>
          {/if}
        {/each}
      </div>
    {/if}

    {#if sources.length}
      <div class="bs-foot">
        <div class="bs-src">{m.a2ui_sources()} {#each sources as s}<span>{s}</span> {/each}</div>
        <div class="bs-upd"><span class="bs-dot"></span> {m.a2ui_updated_just_now()}</div>
      </div>
    {/if}
  </div>
</div>

<style>
  /* Shell (container, masthead, ticker, footer) is shared in broadsheet.css
     (.bs/.bs-*). Only the News-specific ticker item + body styles live here. */
  .h { display: inline-flex; align-items: center; gap: 11px; padding: 6px 17px; font-size: 12px; color: var(--ink-2); }
  .h::before { content: "◆"; color: var(--accent); font-size: 7px; }
  .h b { font-weight: 600; color: var(--ink); }

  .lead { display: grid; grid-template-columns: 1.25fr 1fr; gap: 20px; padding-bottom: 16px; border-bottom: 2px solid var(--ink); }
  .lead.nofig { grid-template-columns: 1fr; }
  .cat { font-family: var(--code); font-size: 9.5px; font-weight: 700; letter-spacing: .2em; text-transform: uppercase; color: var(--accent-d); display: inline-flex; align-items: center; gap: 7px; }
  .cat::before { content: ""; width: 16px; height: 2px; background: var(--accent); }
  .lead h2 { margin: 9px 0 0; font-family: var(--serif); font-weight: 600; font-size: clamp(22px, 4.4vw, 32px); line-height: 1.03; letter-spacing: -.015em; font-variation-settings: "opsz" 100; }
  .lead h2 a { color: inherit; text-decoration: none; }
  .lead h2 a:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 3px; }
  .deck { margin: 10px 0 0; max-width: 48ch; font-size: 13.5px; line-height: 1.5; color: var(--ink-2); }
  /* No figure → single column, so let the deck fill the full width like the
     headline and why-it-matters instead of stopping at the readability cap. */
  .lead.nofig .deck { max-width: none; }
  .why { margin-top: 12px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .why b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }
  .byline { margin-top: 13px; font-family: var(--code); font-size: 10.5px; color: var(--ink-3); letter-spacing: .02em; }
  .byline b { color: var(--ink-2); font-weight: 700; }
  .figure { position: relative; align-self: stretch; min-height: 190px; border: 1px solid var(--rule-2); overflow: hidden; background: var(--paper-2); }
  .figure img { width: 100%; height: 100%; object-fit: cover; display: block; filter: saturate(.92) contrast(1.02); animation: kenburns 20s ease-in-out infinite alternate; }
  @keyframes kenburns { from { transform: scale(1.06); } to { transform: scale(1.16) translate(-2.5%, -2%); } }
  /* Animations = Off: no perpetual Ken Burns (app-wide tier attribute on .app) */
  :global([data-animations='off']) .figure img { animation: none; transform: scale(1.06); }
  .figure figcaption { position: absolute; left: 0; bottom: 0; margin: 0; padding: 4px 9px; background: var(--ink); color: var(--paper); font-family: var(--code); font-size: 8.5px; letter-spacing: .1em; text-transform: uppercase; }

  .more-label { margin: 15px 0 0; font-family: var(--code); font-size: 9.5px; font-weight: 700; letter-spacing: .22em; text-transform: uppercase; color: var(--ink-3); }
  .item { display: grid; grid-template-columns: 28px 1fr auto; gap: 13px; align-items: start; padding: 12px 0; border-top: 1px solid var(--rule); }
  .item[role='button'] { cursor: pointer; }
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


  @media (max-width: 640px) {
    .lead { grid-template-columns: 1fr; }
    .figure { min-height: 160px; order: -1; }
    .item { grid-template-columns: 24px 1fr; }
    .thumb, .item .chev { display: none; }
  }
</style>
