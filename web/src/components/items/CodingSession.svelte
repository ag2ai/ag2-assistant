<script lang="ts">
  // "The Workshop" — editorial rendering of a CodingSession A2UI surface: a host
  // CLI coding agent (Claude Code / Codex / OpenCode, driven over ACP) writing
  // code in an approved repo. The masthead shows a COMPACT derived title (the
  // full task brief folds away below — task prompts are long); while the agent
  // works, a live strip with a blinking caret carries the state. Then the plan
  // and the working-tree diff as full unified hunks. Shares the .bs editorial
  // shell with the other surfaces.
  import { rows, str } from '../../lib/a2ui.ts'
  import type { A2UIData, CodingFile, CodingPlanStep } from '../../lib/a2ui.ts'
  import { m } from '../../paraglide/messages.js'

  type Props = { data?: A2UIData }
  let { data = {} }: Props = $props()

  const agent = $derived(str(data.agent) || m.a2ui_coding_agent())
  const directory = $derived(str(data.directory))
  const task = $derived(str(data.task) || m.a2ui_coding_session())
  const status = $derived((str(data.status) || 'running').toLowerCase())
  const summary = $derived(str(data.summary))
  const error = $derived(str(data.error))
  const plan = $derived(rows<CodingPlanStep>(data.plan))
  const files = $derived(rows<CodingFile>(data.files))

  // The status value drives the chip class; only its label localizes.
  const STATUS: Record<string, { label: () => string; cls: string } | undefined> = {
    running: { label: m.a2ui_working, cls: 'run' },
    done: { label: m.a2ui_done, cls: 'ok' },
    failed: { label: m.a2ui_failed_cap, cls: 'bad' },
  }
  const chip = $derived(STATUS[status] || { label: () => status, cls: 'idle' })
  // Likewise for the per-file change badge (added/modified/deleted).
  const FILE_STATUS: Record<string, (() => string) | undefined> = {
    added: m.a2ui_file_added, modified: m.a2ui_file_modified, deleted: m.a2ui_file_deleted,
  }
  const fileStatus = (v: string) => FILE_STATUS[v]?.() ?? v

  // Headline = first sentence/line of the task, hard-capped; the h1 is a
  // display face — a multi-paragraph prompt does not belong in it.
  function titleOf(text: string) {
    const first = text.trim().split('\n', 1)[0]
    const sentence = first.split(/(?<=[.!?…:])\s+/, 1)[0] || first
    return sentence.length > 96 ? sentence.slice(0, 93).trimEnd() + '…' : sentence
  }
  const title = $derived(titleOf(task))
  const hasBrief = $derived(task.trim().length > title.length)

  const totals = $derived(
    files.reduce((a, f) => ({ add: a.add + (f.added || 0), rem: a.rem + (f.removed || 0) }), { add: 0, rem: 0 })
  )
  const PLAN_MARK: Record<string, string | undefined> = { completed: '✓', in_progress: '▸', pending: '○' }

  // Split a unified-diff string into typed lines for +/- coloring.
  function lines(hunks: string) {
    return String(hunks || '')
      .split('\n')
      .filter((l, i, a) => !(l === '' && i === a.length - 1))
      .map((text) => {
        const c = text[0]
        const kind = text.startsWith('+++') || text.startsWith('---') ? 'meta'
          : c === '@' ? 'hunk'
          : c === '+' ? 'add'
          : c === '-' ? 'del'
          : 'ctx'
        return { text: text || ' ', kind }
      })
  }
</script>

<div class="bs">
  <header class="bs-masthead">
    <div class="head">
      <div class="bs-kicker">A2UI · {m.a2ui_coding()}</div>
      <h1 class="title">{title}</h1>
    </div>
    <div class="bs-edition">
      <div><b>{agent}</b></div>
      <div class="chip {chip.cls}">{chip.label()}</div>
    </div>
  </header>

  {#if status === 'running'}
    <div class="live">
      <span class="caret"></span>
      <span class="who">{agent}</span>
      <span class="doing">{directory ? m.a2ui_writing_code_in() : m.a2ui_writing_code()}</span>
      {#if directory}<code class="where">{directory}</code>{/if}
    </div>
    <div class="beam"></div>
  {/if}

  <div class="bs-body">
    {#if directory && status !== 'running'}<div class="dir">{directory}</div>{/if}

    {#if hasBrief}
      <details class="brief">
        <summary>{m.a2ui_task_brief()}</summary>
        <pre>{task}</pre>
      </details>
    {/if}

    {#if plan.length}
      <ul class="plan">
        {#each plan as p}
          <li class="p-{String(p.status || 'pending').toLowerCase()}">
            <i>{PLAN_MARK[String(p.status || '').toLowerCase()] || '○'}</i>{p.content}
          </li>
        {/each}
      </ul>
    {/if}

    {#if error}
      <div class="err"><b>{m.a2ui_run_failed()}</b>{error}</div>
    {/if}

    {#if files.length}
      <!-- The diff is the long part of the card; fold it like the Task brief.
           The stat line doubles as the toggle. Items are keyed by id in the
           thread, so the open state survives streaming updates. -->
      <details class="fwrap" open>
        <summary class="fstat">{m.a2ui_files_changed({ count: files.length })}
          · <span class="add">+{totals.add}</span> <span class="del">−{totals.rem}</span></summary>
        <div class="files">
          {#each files as f}
            <section class="file">
              <div class="fhead">
                <span class="fst {f.status}">{fileStatus(f.status)}</span>
                <code class="fpath">{f.path}</code>
                <span class="fnum"><span class="add">+{f.added || 0}</span> <span class="del">−{f.removed || 0}</span></span>
              </div>
              {#if f.hunks}
                <pre class="diff">{#each lines(f.hunks) as ln}<span class="ln {ln.kind}">{ln.text}</span>{/each}</pre>
              {:else}
                <div class="nopreview">{m.a2ui_no_preview_binary()}</div>
              {/if}
            </section>
          {/each}
        </div>
      </details>
    {:else if status === 'done'}
      <div class="empty">{m.a2ui_no_changes()}</div>
    {:else if status === 'running' && !plan.length}
      <div class="warming">{m.a2ui_warming()}</div>
    {/if}

    {#if summary && status === 'done'}
      <div class="bs-foot">
        <div class="bs-src">{m.a2ui_from_coding_agent()} — <span>{m.a2ui_diff_not_plan()}</span></div>
        <div class="bs-upd"><span class="bs-dot"></span> {agent}</div>
      </div>
    {/if}
  </div>
</div>

<style>
  /* A long task brief is body copy, not a headline: cap the display face and
     clamp to two lines — the full prompt lives in the "Task brief" fold. */
  .head { min-width: 0; }
  .title { font-size: clamp(17px, 2.6vw, 22px) !important; line-height: 1.12 !important;
    display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

  .chip { flex: none; padding: 2px 8px; font-family: var(--code); font-size: 8.5px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; border: 1px solid currentColor; border-radius: 2px; }
  .chip.ok { color: var(--up-d); }
  .chip.bad { color: var(--accent-d); }
  .chip.run { color: var(--accent-d); animation: chip-pulse 2.2s ease-in-out infinite; }
  .chip.idle { color: var(--ink-3); }
  @keyframes chip-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }

  /* live strip: the "agent at the bench" state while the run streams */
  .live { display: flex; align-items: baseline; gap: 7px; padding: 8px 20px;
    border-top: 1.5px solid var(--ink); background: var(--paper-2);
    font-family: var(--code); font-size: 11px; color: var(--ink-2); min-width: 0; }
  .caret { flex: none; width: 7px; height: 13px; align-self: center; background: var(--accent-d); animation: caret-blink 1.05s steps(1) infinite; }
  @keyframes caret-blink { 0%, 55% { opacity: 1; } 56%, 100% { opacity: .12; } }
  .who { font-weight: 700; color: var(--ink); }
  .doing { flex: none; }
  .where { color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* thin sweeping beam under the live strip — motion says "in progress" */
  .beam { height: 2px; background:
    linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--accent) 65%, transparent) 50%, transparent 100%);
    background-size: 40% 100%; background-repeat: no-repeat; animation: beam-sweep 1.8s ease-in-out infinite; }
  @keyframes beam-sweep { 0% { background-position: -40% 0; } 100% { background-position: 140% 0; } }
  @media (prefers-reduced-motion: reduce) {
    .chip.run, .caret, .beam { animation: none; }
  }

  .dir { font-family: var(--code); font-size: 11px; color: var(--ink-3); margin-top: 2px; word-break: break-all; }

  .brief { margin-top: 10px; border: 1px solid var(--rule); border-radius: 4px; background: color-mix(in srgb, var(--ink) 3%, transparent); }
  .brief summary { cursor: pointer; padding: 6px 10px; font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--ink-3); user-select: none; }
  .brief summary:hover { color: var(--ink-2); }
  .brief pre { margin: 0; padding: 4px 12px 10px; font-family: var(--ui); font-size: 12px; line-height: 1.5; color: var(--ink-2); white-space: pre-wrap; word-break: break-word; }

  .plan { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; border-top: 1px solid var(--rule); padding-top: 10px; }
  .plan li { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; line-height: 1.4; color: var(--ink); }
  .plan i { font-style: normal; font-family: var(--code); font-size: 11px; width: 14px; text-align: center; flex: none; }
  .plan .p-completed i { color: var(--up-d); }
  .plan .p-completed { color: var(--ink-2); }
  .plan .p-in_progress i { color: var(--accent-d); }

  /* the stat line is the <summary> of the diff fold — same affordance as Task brief */
  .fwrap { margin-top: 14px; }
  .fstat { font-family: var(--code); font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); cursor: pointer; user-select: none; }
  .fstat:hover { color: var(--ink-2); }
  .add { color: var(--up-d); font-weight: 700; }
  .del { color: var(--accent-d); font-weight: 700; }

  .files { margin-top: 8px; display: flex; flex-direction: column; gap: 12px; }
  .file { border: 1px solid var(--rule); border-radius: 4px; overflow: hidden; }
  .fhead { display: flex; align-items: center; gap: 10px; padding: 7px 10px; background: color-mix(in srgb, var(--ink) 4%, transparent); border-bottom: 1px solid var(--rule); }
  .fst { flex: none; font-family: var(--code); font-size: 8px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; padding: 1px 6px; border-radius: 2px; border: 1px solid currentColor; }
  .fst.added { color: var(--up-d); }
  .fst.deleted { color: var(--accent-d); }
  .fst.modified { color: var(--ink-2); }
  .fpath { flex: 1; font-family: var(--code); font-size: 12px; color: var(--ink); word-break: break-all; }
  .fnum { flex: none; font-family: var(--code); font-size: 10.5px; }

  .diff { margin: 0; padding: 8px 0; overflow-x: auto; font-family: var(--code); font-size: 11.5px; line-height: 1.5; background: var(--paper); }
  .ln { display: block; padding: 0 10px; white-space: pre; }
  .ln.add { background: color-mix(in srgb, var(--up) 16%, transparent); color: var(--up-d); }
  .ln.del { background: color-mix(in srgb, var(--accent) 12%, transparent); color: var(--accent-d); }
  .ln.hunk { color: var(--ink-3); }
  .ln.meta { color: var(--ink-3); font-weight: 700; }
  .ln.ctx { color: var(--ink-2); }

  .nopreview { padding: 10px; font-family: var(--code); font-size: 11px; color: var(--ink-3); }
  .empty { margin-top: 14px; font-size: 12.5px; color: var(--ink-2); }
  .warming { margin-top: 14px; font-size: 12.5px; color: var(--ink-3); font-style: italic; }
  .err { margin-top: 12px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .err b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }
</style>
