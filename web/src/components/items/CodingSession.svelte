<script>
  // "The Workshop" — editorial rendering of a CodingSession A2UI surface: a host
  // CLI coding agent (Claude Code / Codex / OpenCode, driven over ACP) writing
  // code in an approved repo. Shows the agent's plan, then the working-tree diff
  // as full unified hunks. Shares the .bs editorial shell with the other surfaces.
  let { data = {} } = $props()

  const agent = $derived(data.agent || 'Coding agent')
  const directory = $derived(data.directory || '')
  const task = $derived(data.task || 'Coding session')
  const status = $derived(String(data.status || 'running').toLowerCase())
  const summary = $derived(data.summary || '')
  const error = $derived(data.error || '')
  const plan = $derived((Array.isArray(data.plan) ? data.plan : []).filter(Boolean))
  const files = $derived((Array.isArray(data.files) ? data.files : []).filter(Boolean))

  const STATUS = {
    running: { label: 'Working', cls: 'run' },
    done: { label: 'Done', cls: 'ok' },
    failed: { label: 'Failed', cls: 'bad' },
  }
  const chip = $derived(STATUS[status] || { label: status, cls: 'idle' })

  const totals = $derived(
    files.reduce((a, f) => ({ add: a.add + (f.added || 0), rem: a.rem + (f.removed || 0) }), { add: 0, rem: 0 })
  )
  const PLAN_MARK = { completed: '✓', in_progress: '▸', pending: '○' }

  // Split a unified-diff string into typed lines for +/- coloring.
  function lines(hunks) {
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
    <div>
      <div class="bs-kicker">A2UI · Coding</div>
      <h1>{task}</h1>
    </div>
    <div class="bs-edition">
      <div><b>{agent}</b></div>
      <div class="chip {chip.cls}">{chip.label}</div>
    </div>
  </header>

  <div class="bs-body">
    {#if directory}<div class="dir">{directory}</div>{/if}

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
      <div class="err"><b>Run failed</b>{error}</div>
    {/if}

    {#if files.length}
      <div class="fstat">{files.length} file{files.length === 1 ? '' : 's'} changed
        · <span class="add">+{totals.add}</span> <span class="del">−{totals.rem}</span></div>
      <div class="files">
        {#each files as f}
          <section class="file">
            <div class="fhead">
              <span class="fst {f.status}">{f.status}</span>
              <code class="fpath">{f.path}</code>
              <span class="fnum"><span class="add">+{f.added || 0}</span> <span class="del">−{f.removed || 0}</span></span>
            </div>
            {#if f.hunks}
              <pre class="diff">{#each lines(f.hunks) as ln}<span class="ln {ln.kind}">{ln.text}</span>{/each}</pre>
            {:else}
              <div class="nopreview">no preview (binary or large file)</div>
            {/if}
          </section>
        {/each}
      </div>
    {:else if status === 'done'}
      <div class="empty">No file changes were made.</div>
    {/if}

    {#if summary && status === 'done'}
      <div class="bs-foot">
        <div class="bs-src">From the coding agent — <span>working-tree diff, not a plan</span></div>
        <div class="bs-upd"><span class="bs-dot"></span> {agent}</div>
      </div>
    {/if}
  </div>
</div>

<style>
  .chip { flex: none; padding: 2px 8px; font-family: var(--code); font-size: 8.5px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; border: 1px solid currentColor; border-radius: 2px; }
  .chip.ok { color: var(--up-d); }
  .chip.bad { color: var(--accent-d); }
  .chip.run { color: var(--ink-2); }
  .chip.idle { color: var(--ink-3); }

  .dir { font-family: var(--code); font-size: 11px; color: var(--ink-3); margin-top: 2px; word-break: break-all; }

  .plan { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; border-top: 1px solid var(--rule); padding-top: 10px; }
  .plan li { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; line-height: 1.4; color: var(--ink); }
  .plan i { font-style: normal; font-family: var(--code); font-size: 11px; width: 14px; text-align: center; flex: none; }
  .plan .p-completed i { color: var(--up-d); }
  .plan .p-completed { color: var(--ink-2); }
  .plan .p-in_progress i { color: var(--accent-d); }

  .fstat { margin-top: 14px; font-family: var(--code); font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
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
  .err { margin-top: 12px; padding-left: 12px; border-left: 2px solid var(--accent); font-size: 12.5px; line-height: 1.5; color: var(--ink); }
  .err b { font-family: var(--code); font-size: 9px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-d); display: block; margin-bottom: 2px; }
</style>
