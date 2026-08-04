<script lang="ts">
  // "The Docket" — editorial broadsheet rendering of a TaskProgress A2UI surface:
  // a status board for the profile's durable tasks (the GUI projecting real task
  // state the agent gathered via list_tasks/get_task). Shares the .bs shell with
  // the other editorial surfaces. No ticker — the docket is a ledger, not a feed.
  // Rows carrying a task id link through to the live task page (/t/<id>).
  import { go } from '../../router.ts'
  import { rows, str } from '../../lib/a2ui.ts'
  import type { A2UIData, TaskRow } from '../../lib/a2ui.ts'

  type Props = { data?: A2UIData }
  let { data = {} }: Props = $props()

  const title = $derived(str(data.title) || 'Your tasks')
  const tasks = $derived(rows<TaskRow>(data.tasks))

  const edition = $derived(
    new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  )

  const STATUS: Record<string, { label: string; cls: string } | undefined> = {
    active: { label: 'Active', cls: 'ok' },
    scheduled: { label: 'Scheduled', cls: 'idle' },
    completed: { label: 'Completed', cls: 'done' },
    stopped: { label: 'Stopped', cls: 'idle' },
    failed: { label: 'Failing', cls: 'bad' },
  }
  const chip = (t: TaskRow) => STATUS[String(t.status || '').toLowerCase()] || { label: t.status || '—', cls: 'idle' }
  const MARK: Record<string, string | undefined> = { done: '✓', pending: '○', failed: '✕' }
  const activeCount = $derived(tasks.filter((t) => ['active', 'scheduled'].includes(String(t.status).toLowerCase())).length)
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="mast-l">
      <div class="bs-kicker">A2UI · Tasks</div>
      <h1>{title}</h1>
    </div>
    <div class="bs-edition">
      <div>{edition}</div>
      <div><b>{activeCount} running · {tasks.length} total</b></div>
    </div>
  </header>

  <div class="bs-body">
    <div class="docket">
      {#each tasks as t}
        <section class="task">
          <div class="thead">
            <span class="chip {chip(t).cls}">{chip(t).label}</span>
            <h3>
              {#if t.id}
                <button class="tlink" onclick={() => go('/t/' + t.id)} title="Open this task">{t.title}</button>
              {:else}{t.title}{/if}
            </h3>
            <div class="when">
              {#if t.schedule}<span>{t.schedule}</span>{/if}
              {#if t.nextRun}<span class="next">next {t.nextRun}</span>{/if}
            </div>
          </div>
          {#if t.objective}<p class="obj">{t.objective}</p>{/if}
          {#if (t.deliverables || []).length}
            <ul class="dels">
              {#each t.deliverables as d}
                <li class="d-{String(d.status || 'pending').toLowerCase()}">
                  <i>{MARK[String(d.status || '').toLowerCase()] || '○'}</i>{d.description}
                </li>
              {/each}
            </ul>
          {/if}
          {#if t.error}
            <div class="err"><b>Needs attention</b>{t.error}</div>
          {:else if t.progress}
            <div class="prog">{t.progress}</div>
          {/if}
        </section>
      {/each}
    </div>

    <div class="bs-foot">
      <div class="bs-src">From the task ledger — <span>live state, not a plan</span></div>
      <div class="bs-upd"><span class="bs-dot"></span> as of just now</div>
    </div>
  </div>
</div>

<style>
  /* Shell (container, masthead, footer) is shared in broadsheet.css. */
  .docket { border-top: 1.5px solid var(--ink); }
  .task { padding: 13px 0 14px; border-bottom: 1px solid var(--rule); }

  .thead { display: flex; align-items: baseline; gap: 11px; flex-wrap: wrap; }
  .chip { flex: none; padding: 2px 8px; font-family: var(--code); font-size: 8.5px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; border: 1px solid currentColor; border-radius: 2px; }
  .chip.ok { color: var(--up-d); }
  .chip.bad { color: var(--accent-d); }
  .chip.done { color: var(--ink); }
  .chip.idle { color: var(--ink-3); }
  .thead h3 { margin: 0; flex: 1; min-width: 12ch; font-family: var(--serif); font-weight: 600; font-size: 17px; line-height: 1.15; letter-spacing: -.01em; }
  .tlink { all: unset; cursor: pointer; }
  .tlink:hover { text-decoration: underline; text-decoration-color: var(--accent); text-underline-offset: 3px; color: var(--accent-d); }
  .tlink:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .when { font-family: var(--code); font-size: 10.5px; color: var(--ink-3); display: inline-flex; gap: 10px; }
  .when .next { color: var(--ink-2); font-weight: 600; }

  .obj { margin: 6px 0 0; font-size: 12.5px; line-height: 1.5; color: var(--ink-2); max-width: 64ch; }

  .dels { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .dels li { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; line-height: 1.4; color: var(--ink); }
  .dels i { font-style: normal; font-family: var(--code); font-size: 11px; width: 14px; text-align: center; flex: none; }
  .dels .d-done i { color: var(--up-d); }
  .dels .d-failed i { color: var(--accent-d); }
  .dels .d-pending i, .dels .d-pending { color: var(--ink-2); }

  .prog { margin-top: 8px; font-family: var(--code); font-size: 11px; color: var(--ink-3); }
  .prog::before { content: "→ "; color: var(--accent); }
  .err { margin-top: 9px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .err b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }
</style>
