<script>
  // "The Day" — editorial broadsheet rendering of an AgendaCard A2UI surface:
  // one day's calendar as a ruled timeline. All-day items ride as chips under the
  // masthead; the single next-up event gets the accent treatment. Data comes from
  // the calendar tools (source-agnostic: the card renders whatever fed it).
  // Event/join links are agent-produced URLs → gated through safeUrl (http(s) only).
  import { safeUrl } from '../../lib/url.js'

  let { data = {} } = $props()

  const title = $derived(data.title || 'Agenda')
  const events = $derived((Array.isArray(data.events) ? data.events : []).filter(Boolean))
  const allDay = $derived(events.filter((e) => e.allDay))
  const timed = $derived(events.filter((e) => !e.allDay))
  const note = $derived(data.note || '')

  const date = $derived(
    data.date ||
      new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' })
  )
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · Agenda</div>
      <h1>{title}</h1>
    </div>
    <div class="bs-edition">
      <div>{date}</div>
      <div><b>{timed.length} {timed.length === 1 ? 'event' : 'events'}{allDay.length ? ` · ${allDay.length} all-day` : ''}</b></div>
    </div>
  </header>

  <div class="bs-body">
    {#if allDay.length}
      <div class="allday">
        {#each allDay as e}
          <span class="adchip">{e.title}{#if e.location} · {e.location}{/if}</span>
        {/each}
      </div>
    {/if}

    {#if timed.length}
      <div class="timeline">
        {#each timed as e}
          <div class="slot" class:next={e.next}>
            <div class="when">
              <div class="t1">{e.start || ''}</div>
              {#if e.end}<div class="t2">{e.end}</div>{/if}
            </div>
            <div class="what">
              {#if e.next}<span class="upnext">Up next</span>{/if}
              <div class="etitle">
                {#if safeUrl(e.url)}<a href={safeUrl(e.url)} target="_blank" rel="noopener noreferrer">{e.title}</a>{:else}{e.title}{/if}
              </div>
              {#if e.location}<div class="eloc">{e.location}</div>{/if}
              {#if safeUrl(e.joinUrl)}<a class="join" href={safeUrl(e.joinUrl)} target="_blank" rel="noopener noreferrer">Join meeting →</a>{/if}
            </div>
          </div>
        {/each}
      </div>
    {:else}
      <p class="clear">Nothing scheduled — the day is yours.</p>
    {/if}

    {#if note}<div class="note"><b>The shape of it</b>{note}</div>{/if}

    <div class="bs-foot">
      <div class="bs-src">From your calendar — <span>events as returned, times local</span></div>
      <div class="bs-upd"><span class="bs-dot"></span> as of just now</div>
    </div>
  </div>
</div>

<style>
  /* Shell (container, masthead, footer) is shared in broadsheet.css. */
  .allday { display: flex; flex-wrap: wrap; gap: 7px; padding: 12px 0 2px; }
  .adchip { padding: 4px 11px; border: 1px solid var(--rule-2); border-radius: 999px; font-family: var(--code); font-size: 10.5px; font-weight: 600; letter-spacing: .04em; color: var(--ink-2); background: var(--paper-2); }

  .timeline { margin-top: 10px; border-top: 1.5px solid var(--ink); }
  .slot { display: grid; grid-template-columns: 92px 1fr; gap: 14px; padding: 11px 0 12px; border-bottom: 1px solid var(--rule); }
  .slot.next { box-shadow: inset 3px 0 0 var(--accent); background: color-mix(in srgb, var(--accent) 4%, transparent); padding-left: 12px; }

  .when { font-family: var(--code); text-align: right; padding-top: 2px; }
  .t1 { font-size: 13px; font-weight: 700; color: var(--ink); }
  .t2 { font-size: 10.5px; color: var(--ink-3); margin-top: 2px; }
  .t2::before { content: "– "; }

  .upnext { display: inline-block; margin-bottom: 3px; padding: 2px 7px; background: var(--accent); color: var(--paper); font-family: var(--code); font-size: 8px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
  .etitle { font-family: var(--serif); font-weight: 600; font-size: 16.5px; line-height: 1.15; letter-spacing: -.01em; color: var(--ink); }
  .etitle a { color: inherit; text-decoration: none; }
  .etitle a:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 3px; color: var(--accent-d); }
  .eloc { margin-top: 3px; font-family: var(--code); font-size: 10.5px; color: var(--ink-3); }
  .eloc::before { content: "◈ "; color: var(--accent); }
  .join { display: inline-block; margin-top: 6px; padding: 3px 10px; border: 1px solid var(--accent); border-radius: 999px; font-family: var(--code); font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--accent-d); text-decoration: none; }
  .join:hover { background: var(--accent); color: var(--paper); }

  .clear { margin: 14px 0 0; font-family: var(--serif); font-size: 15px; color: var(--ink-2); font-style: italic; }

  .note { margin-top: 14px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .note b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }
</style>
