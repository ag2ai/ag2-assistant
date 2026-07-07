<script>
  // "The Post" — editorial broadsheet rendering of an InboxBrief A2UI surface:
  // an inbox digest as ruled correspondence rows. Unread mail gets the accent
  // dot + bold sender; mail that clearly asks for something carries a
  // "Reply?" flag. Subjects link to the thread in Gmail — agent-produced URLs,
  // so they pass through the safeUrl scheme guard.
  import { safeUrl } from '../../lib/url.js'

  let { data = {} } = $props()

  const title = $derived(data.title || 'Inbox')
  const threads = $derived((Array.isArray(data.threads) ? data.threads : []).filter(Boolean))
  const summary = $derived(data.summary || '')
  const unreadCount = $derived(threads.filter((t) => t.unread).length)

  const edition = $derived(
    new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' })
  )
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · Inbox</div>
      <h1>{title}</h1>
    </div>
    <div class="bs-edition">
      <div>{edition}</div>
      <div><b>{threads.length} {threads.length === 1 ? 'thread' : 'threads'}{unreadCount ? ` · ${unreadCount} unread` : ''}</b></div>
    </div>
  </header>

  <div class="bs-body">
    {#if summary}<p class="deck">{summary}</p>{/if}

    <div class="post">
      {#each threads as t}
        <div class="mail" class:unread={t.unread}>
          <span class="dot" class:on={t.unread}></span>
          <div class="mfrom">{t.from}</div>
          <div class="mmain">
            <div class="msub">
              {#if safeUrl(t.url)}<a href={safeUrl(t.url)} target="_blank" rel="noopener noreferrer">{t.subject}</a>{:else}{t.subject}{/if}
              {#if t.needsReply}<span class="reply">Reply?</span>{/if}
            </div>
            {#if t.gist}<div class="mgist">{t.gist}</div>{/if}
          </div>
          <div class="mwhen">{t.when || ''}</div>
        </div>
      {/each}
    </div>

    <div class="bs-foot">
      <div class="bs-src">From your mailbox — <span>threads as returned</span></div>
      <div class="bs-upd"><span class="bs-dot"></span> as of just now</div>
    </div>
  </div>
</div>

<style>
  /* Shell (container, masthead, footer) is shared in broadsheet.css. */
  .deck { margin: 12px 0 0; max-width: 62ch; font-size: 13.5px; line-height: 1.5; color: var(--ink-2); }

  .post { margin-top: 12px; border-top: 1.5px solid var(--ink); }
  .mail { display: grid; grid-template-columns: 14px minmax(110px, 0.55fr) 2fr auto; gap: 12px; align-items: baseline; padding: 11px 0 12px; border-bottom: 1px solid var(--rule); }

  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--rule-2); align-self: center; }
  .dot.on { background: var(--accent); }

  .mfrom { font-family: var(--code); font-size: 11.5px; font-weight: 600; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mail.unread .mfrom { color: var(--ink); font-weight: 700; }

  .msub { font-family: var(--serif); font-weight: 500; font-size: 15.5px; line-height: 1.2; letter-spacing: -.005em; color: var(--ink); }
  .mail.unread .msub { font-weight: 650; }
  .msub a { color: inherit; text-decoration: none; }
  .msub a:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 3px; color: var(--accent-d); }
  .reply { margin-left: 8px; padding: 2px 8px; background: var(--accent); color: var(--paper); font-family: var(--code); font-size: 8px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; vertical-align: 2px; }
  .mgist { margin-top: 3px; font-size: 12px; line-height: 1.45; color: var(--ink-2); }

  .mwhen { font-family: var(--code); font-size: 10.5px; color: var(--ink-3); white-space: nowrap; }

  @media (max-width: 640px) {
    .mail { grid-template-columns: 12px 1fr auto; }
    .mfrom { display: none; }
  }
</style>
