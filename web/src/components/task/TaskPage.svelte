<script>
  // The task's page: a Cowork-style read-only view (name / description / status /
  // instructions / working folder / schedule / approvals) + its run history.
  // Editing config lives in TaskEditModal, opened over this page. `/t/new` is the
  // same page with the modal open over an empty view — one route, one component,
  // for both create and edit. Each run opens as a chat thread at /r/{id}.
  import { api } from '../../transport/api.js'
  import { go, route } from '../../router.js'
  import Icon from '../Icon.svelte'
  import TaskEditModal from './TaskEditModal.svelte'
  import { fmtStamp, fmtNextIn } from '../../lib/time.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']

  let task = $state(null)     // server copy (null while loading / for 'new')
  let perms = $state([])      // this task's always-allowed command rules
  let running = $state(false)
  let pausing = $state(false)
  let confirmDel = $state(false)
  let editOpen = $state(false)
  let error = $state('')

  const isNew = $derived($route.id === 'new')

  // Monotonic token: fast task-A → task-B navigation can let A's load() await
  // resolve after B's has started. Each call claims the next token and checks
  // it's still current before committing ANY state — `task`/`perms` are always
  // written together from locally-held results, never from a bare awaited
  // value, so a superseded call can't leave `task` = A while `perms` = B.
  let _loadSeq = 0
  async function load(id) {
    const seq = ++_loadSeq
    error = ''
    confirmDel = false
    if (id === 'new') { task = null; perms = []; return }
    try {
      const [t, p] = await Promise.all([api.task(id), api.taskPermissions(id).catch(() => [])])
      if (seq !== _loadSeq) return
      task = t
      perms = p
    } catch { if (seq === _loadSeq) { error = 'Task not found.'; task = null; perms = [] } }
  }
  // reload when the route's id changes
  let _lastId = ''
  $effect(() => { const id = $route.id; if (id !== _lastId) { _lastId = id; load(id) } })

  async function runNow() {
    if (running || !task) return
    running = true
    try { const run = await api.runTask(task.id); go('/r/' + run.id) }
    catch (e) { error = e.message || 'run failed' } finally { running = false }
  }

  async function togglePause() {
    if (!task || pausing) return
    pausing = true
    try { task = await api.updateTask(task.id, { paused: !task.paused }) }
    catch (e) { error = e.message || 'pause failed' } finally { pausing = false }
  }

  async function del() {
    if (!task) return
    try { await api.deleteTask(task.id); go('/tasks') } catch (e) { error = e.message }
  }

  async function revoke(rule) {
    if (!task) return
    try { await api.deleteTaskPermission(task.id, rule); perms = await api.taskPermissions(task.id) }
    catch (e) { error = e.message || 'revoke failed' }
  }

  // isNew: closing/saving the modal has nowhere else to land on but /tasks / the
  // new task's own page. Editing: the modal just closes back over this page,
  // with the fresh copy from the save swapped straight into `task`.
  function closeModal() { if (isNew) go('/tasks'); else editOpen = false }
  function onModalSaved(t) { if (isNew) go('/t/' + t.id); else { task = t; editOpen = false } }

  // Status → icon, matching Drawer.svelte's status-glyph conventions.
  const STAT_ICON = { running: 'spinner', needs_input: 'help-circle', completed: 'check', failed: 'x', cancelled: 'slash' }
</script>

<div class="thread taskpage">
  <div class="inner">
    <div class="crumbs"><button onclick={() => go('/tasks')}>Tasks</button> / {task?.name || (isNew ? 'New task' : '')}</div>

    {#if error}<div class="taskerror"><Icon name="x" size={13} /> {error}</div>{/if}

    {#if task}
      <div class="tphead">
        <div>
          <h1>{task.name}</h1>
          {#if task.description}<div class="tpdesc">{task.description}</div>{/if}
          <div class="tpstatus">
            <label class="tpswitch">
              <input type="checkbox" checked={!task.paused} disabled={pausing} onchange={togglePause} />
              <span class="tpknob"></span>
            </label>
            <span class="badge" class:paused={task.paused}>{task.paused ? 'Paused' : 'Active'}</span>
            {#if task.next_run_at && !task.paused}<span class="muted">Next run: {fmtNextIn(task.next_run_at)}</span>{/if}
          </div>
        </div>
        <div class="tpactions">
          <button class="iconbtn" title="Edit" onclick={() => (editOpen = true)}><Icon name="pencil" size={15} /></button>
          {#if confirmDel}
            <span class="delconfirm">
              <span class="confirm">Delete permanently?</span>
              <button class="open danger" onclick={del}>Yes, delete</button>
              <button class="open" onclick={() => (confirmDel = false)}>Cancel</button>
            </span>
          {:else}
            <button class="iconbtn" title="Delete" onclick={() => (confirmDel = true)}><Icon name="trash" size={15} /></button>
          {/if}
          <button class="open primary" disabled={running} onclick={runNow}>
            <Icon name="play" size={14} /> {running ? 'Starting…' : 'Run now'}
          </button>
        </div>
      </div>

      <div class="tpcols">
        <section>
          <h2>History</h2>
          {#if !task.runs.length}<div class="none">No runs yet — hit Run now, or wait for the schedule.</div>{/if}
          <div class="runslist">
            {#each task.runs as r (r.id)}
              <button class="runrow" class:unseen={TERMINAL.includes(r.status) && !r.seen} onclick={() => go('/r/' + r.id)}>
                <span class="statusicon {r.status}"><Icon name={STAT_ICON[r.status] || 'clock'} size={13} /></span>
                <span class="runwhen">{fmtStamp(r.started_at)}</span>
                <span class="runsum">{r.summary || r.error || r.status}</span>
              </button>
            {/each}
          </div>
        </section>
        <section>
          <h2>Instructions</h2>
          <p class="tpprompt">{task.prompt}</p>
          <h2>Working folder</h2>
          <p class="tpmeta">{task.workdir ? `${task.workdir} (${task.workdir_access === 'read_write' ? 'read-write' : 'read-only'})` : '—'}</p>
          <h2>Repeats</h2>
          <p class="tpmeta">{task.schedule_desc}</p>
          <h2>Always allowed</h2>
          {#if perms.length}
            {#each perms as rule (rule)}
              <div class="permrow">
                <code>{rule}</code>
                <button class="iconbtn" title="Revoke" onclick={() => revoke(rule)}><Icon name="x" size={13} /></button>
              </div>
            {/each}
          {:else}
            <p class="muted">Approvals you grant during a run appear here.</p>
          {/if}
        </section>
      </div>
    {:else if !isNew}
      <div class="none">Loading…</div>
    {/if}
  </div>
</div>

{#if editOpen || isNew}
  <TaskEditModal task={isNew ? null : task} onSaved={onModalSaved} onClose={closeModal} />
{/if}

<style>
  /* Reuses the .thread/.inner shell (Thread.svelte) so the page reads as the same
     scrolling column with the same max-width/centering as the chat/run thread,
     instead of a bespoke full-bleed panel. */
  .taskpage { padding: 28px 0 60px; overflow-y: auto; }

  .crumbs { font-size: 12px; color: var(--muted); margin-bottom: 14px; }
  .crumbs button { border: none; background: none; padding: 0; font: inherit; color: var(--muted); cursor: pointer; }
  .crumbs button:hover { color: var(--accent); }

  .tphead { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .tphead h1 { margin: 0 0 4px; font-size: var(--text-2xl); color: var(--ink); }
  .tpdesc { color: var(--muted); font-size: 13px; margin-bottom: 8px; max-width: 60ch; }
  .tpstatus { display: flex; align-items: center; gap: 10px; }
  .tpactions { display: flex; align-items: center; gap: 8px; flex: none; }

  /* A 2-position pill toggle for Active/Paused — same knob-on-a-track vocabulary
     as AccessSwitch's .sw3, simplified to a plain checkbox (no cycling states). */
  .tpswitch { position: relative; display: inline-flex; flex: none; width: 34px; height: 20px; cursor: pointer; }
  .tpswitch input { position: absolute; inset: 0; margin: 0; opacity: 0; cursor: pointer; z-index: 1; }
  .tpknob { position: absolute; inset: 0; border-radius: 999px; background: color-mix(in srgb, var(--ink) 10%, var(--surface)); box-shadow: inset 0 0 0 1px var(--line); transition: background var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out); }
  .tpknob::after { content: ''; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, .3); transition: transform var(--dur-fast) var(--ease-out); }
  .tpswitch input:checked + .tpknob { background: color-mix(in srgb, var(--accent) 55%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 40%, var(--line)); }
  .tpswitch input:checked + .tpknob::after { transform: translateX(14px); }
  .tpswitch input:disabled + .tpknob { opacity: .6; }

  /* .badge (app.css) is accent-tinted by default (used elsewhere for "on" states);
     Paused reads as a neutral/off state instead. */
  .badge.paused { border-color: var(--line); background: var(--surface); color: var(--muted); }
  .muted { color: var(--muted); font-size: 13px; }

  .tpcols { display: grid; grid-template-columns: 1.1fr 1fr; gap: 32px; margin-top: 24px; align-items: start; }
  @media (max-width: 760px) { .tpcols { grid-template-columns: 1fr; } }
  .tpcols h2 { margin: 20px 0 8px; font-size: 12px; font-weight: var(--fw-semibold); color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
  .tpcols section > h2:first-child { margin-top: 0; }
  .tpprompt { white-space: pre-wrap; font-size: 13px; line-height: 1.5; color: var(--ink); margin: 0; }
  .tpmeta { font-size: 13px; color: var(--ink); margin: 0; }

  .permrow { display: flex; align-items: center; gap: 8px; border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 6px 10px; margin-bottom: 6px; }
  .permrow code { flex: 1; min-width: 0; font-family: var(--mono); font-size: 12px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Icon-only action buttons (Edit / Delete / revoke a permission rule) — same
     bordered-square shape as the composer's icon buttons, sized for a 15px glyph. */
  .iconbtn { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 30px; height: 30px; padding: 0; border: 1px solid var(--line); background: var(--surface); color: var(--muted); border-radius: var(--radius-sm); cursor: pointer; transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out); }
  .iconbtn:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); color: var(--accent); background: var(--accent-soft); }
  .iconbtn:disabled { opacity: .5; cursor: default; }

  /* Run rows: same bordered-card row vocabulary as Settings' .llmrow / .mcprow
     (border + radius-sm + hover fill), with the .drow.unseen accent-tint treatment
     for runs the user hasn't opened yet. */
  .runslist { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .runrow {
    display: flex; gap: 10px; align-items: center; width: 100%; text-align: left;
    border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface);
    padding: 9px 12px; cursor: pointer; font: inherit;
    transition: border-color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
  }
  .runrow:hover { background: var(--code); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
  .runrow:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .runrow.unseen { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .runrow.unseen .runsum { color: var(--ink); font-weight: var(--fw-semibold); }
  .runsum { flex: 1; min-width: 0; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* The following were app.css rules scoped to an ancestor (.panel/.modal/.drow)
     that TaskPanel.svelte's root supplied — TaskPage's root carries neither class,
     so they're reproduced here (Svelte-scoped to this component only). */
  .open {
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    flex: none; font-family: var(--sans); font-size: 13px; font-weight: var(--fw-medium); cursor: pointer;
    border: 1px solid var(--line); background: var(--surface); color: var(--ink);
    border-radius: var(--radius-sm); padding: 7px 14px;
    transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
  }
  .open:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); color: var(--accent); background: var(--accent-soft); }
  .open:active { background: var(--code); }
  .open:disabled { opacity: .5; cursor: default; border-color: var(--line); color: var(--muted); background: var(--surface); }
  .open.danger { border-color: color-mix(in srgb, #d8552f 55%, var(--line)); color: #d8552f; background: none; }
  .open.danger:hover { border-color: #d8552f; color: #fff; background: #d8552f; }
  /* The one CTA that matters (Run now) gets the accent-outlined ".open.primary"
     treatment already used for Codex's single primary action. */
  .taskpage .open.primary { border-color: var(--accent); color: var(--accent); }
  .taskpage .open.primary:hover:not(:disabled) { background: var(--accent-soft); }
  .delconfirm { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .delconfirm .confirm { color: #d8552f; font-size: 13px; }
  .taskerror { display: flex; align-items: flex-start; gap: 7px; margin-bottom: 8px; padding: 9px 11px; font-size: var(--text-sm); line-height: var(--leading-snug); color: var(--ag2-observer); background: color-mix(in srgb, var(--ag2-observer) 9%, transparent); border: 1px solid color-mix(in srgb, var(--ag2-observer) 28%, transparent); border-radius: var(--radius-sm); overflow-wrap: anywhere; }
  .statusicon { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 16px; color: var(--muted); }
  .statusicon.completed { color: #3ba55d; }
  .statusicon.failed { color: #d8552f; }
  .statusicon.cancelled { color: var(--muted); }
  .statusicon.running { color: var(--info); }
  .statusicon.needs_input { color: var(--accent); }
  .runwhen { flex: none; color: var(--muted); font-size: 13px; }
</style>
