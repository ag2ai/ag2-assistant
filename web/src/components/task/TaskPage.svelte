<script>
  // The task's page: an always-editable config (name / prompt / model / schedule)
  // + its runs. `/t/new` is the same page with an empty draft — one UX for
  // create and edit. Each run opens as a chat thread at /r/{id}.
  import { api } from '../../transport/api.js'
  import { go, route } from '../../router.js'
  import Icon from '../Icon.svelte'
  import ScheduleField from './ScheduleField.svelte'
  import { fmtStamp } from '../../lib/time.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']

  let task = $state(null)          // server copy (null while loading / for 'new')
  let draft = $state(null)         // editable copy
  let models = $state([])          // llm_configs entries for the model dropdown
  let saving = $state(false)
  let running = $state(false)
  let pausing = $state(false)
  let confirmDel = $state(false)
  let error = $state('')

  const isNew = $derived($route.id === 'new')
  const dirty = $derived(
    draft && (isNew || JSON.stringify(strip(draft)) !== JSON.stringify(strip(task)))
  )
  function strip(t) {
    return t && { name: t.name, prompt: t.prompt, model: t.model, schedule: t.schedule, paused: t.paused }
  }

  // Monotonic token: fast task-A → task-B navigation can let A's load() await
  // resolve after B's has started. Each call claims the next token and checks
  // it's still current before committing ANY state — `task`/`draft` are always
  // written together from a locally-held result, never from a bare awaited
  // value, so a superseded call can't leave `task` = A while `draft` = B (the
  // pair save()/togglePause()/del() key off task.id).
  let _loadSeq = 0
  async function load(id) {
    const seq = ++_loadSeq
    error = ''
    confirmDel = false
    try { models = ((await api.llmConfigs()).configs) || [] } catch { models = [] }
    if (seq !== _loadSeq) return   // superseded mid-flight — this is a stale nav's data
    if (id === 'new') {
      task = null
      draft = { name: '', prompt: '', model: null, schedule: { kind: 'manual', at: null, cron: null }, paused: false }
      return
    }
    try {
      const t = await api.task(id)
      if (seq !== _loadSeq) return
      task = t
      draft = structuredClone(strip(t))
    } catch { if (seq === _loadSeq) { error = 'Task not found.'; task = null; draft = null } }
  }
  // reload when the route's id changes
  let _lastId = ''
  $effect(() => { const id = $route.id; if (id !== _lastId) { _lastId = id; load(id) } })

  async function save() {
    if (!draft || saving) return
    saving = true
    error = ''
    try {
      if (isNew) {
        const created = await api.createTask({ name: draft.name, prompt: draft.prompt, model: draft.model ?? '', schedule: draft.schedule })
        go('/t/' + created.id)
      } else {
        task = await api.updateTask(task.id, { ...strip(draft), model: draft.model ?? '' })
        draft = structuredClone(strip(task))
      }
    } catch (e) { error = e.message || 'save failed' } finally { saving = false }
  }

  async function runNow() {
    if (running || isNew) return
    running = true
    try { const run = await api.runTask(task.id); go('/r/' + run.id) }
    catch (e) { error = e.message || 'run failed' } finally { running = false }
  }

  async function togglePause() {
    if (isNew || pausing) return
    pausing = true
    try {
      task = await api.updateTask(task.id, { paused: !task.paused })
      draft = structuredClone(strip(task))
    } catch (e) { error = e.message || 'pause failed' } finally { pausing = false }
  }

  async function del() {
    try { await api.deleteTask(task.id); go('/tasks') } catch (e) { error = e.message }
  }

  // Icon.svelte's PATHS set doesn't include loader/help-circle/slash/circle/play/pause —
  // substituted for the nearest available glyph, matching Drawer.svelte's Task 11
  // conventions (running→zap, needs_input→message; the paused-status square doubles
  // here as the "stop" action icon) and 'chevron-right' standing in for a play triangle.
  const STAT_ICON = { running: 'zap', needs_input: 'message', completed: 'check', failed: 'x', cancelled: 'square' }
</script>

<div class="thread taskpage">
  <div class="tphead">
    <h1>{isNew ? 'New task' : (task?.name || '…')}</h1>
    {#if !isNew && task}
      <div class="tpactions">
        <button class="open" disabled={running} onclick={runNow}>
          <Icon name="chevron-right" size={14} /> {running ? 'Starting…' : 'Run now'}
        </button>
        <button class="open" disabled={pausing} onclick={togglePause}>
          <Icon name={task.paused ? 'chevron-right' : 'square'} size={14} />
          {pausing ? (task.paused ? 'Resuming…' : 'Pausing…') : task.paused ? 'Resume' : 'Pause'}
        </button>
        {#if confirmDel}
          <span class="delconfirm">
            <span class="confirm">Delete permanently?</span>
            <button class="open danger" onclick={del}>Yes, delete</button>
            <button class="open" onclick={() => (confirmDel = false)}>Cancel</button>
          </span>
        {:else}
          <button class="open danger" onclick={() => (confirmDel = true)}><Icon name="trash" size={14} /> Delete</button>
        {/if}
      </div>
    {/if}
  </div>

  {#if error}<div class="taskerror"><Icon name="x" size={13} /> {error}</div>{/if}

  {#if draft}
    <div class="tpconfig">
      <label>Name
        <input type="text" bind:value={draft.name} placeholder="Daily digest" />
      </label>
      <label>Prompt
        <textarea rows="6" bind:value={draft.prompt}
          placeholder="What should the agent do on every run? Be specific — it runs unattended."></textarea>
      </label>
      <label>Model
        <select bind:value={draft.model}>
          <option value={null}>Profile default</option>
          {#each models as m (m.id)}<option value={m.id}>{m.name} ({m.model})</option>{/each}
        </select>
      </label>
      <label>Schedule
        <ScheduleField bind:schedule={draft.schedule} />
      </label>
      {#if task?.next_run_at && !task.paused}<div class="note">Next run: {fmtStamp(task.next_run_at)}</div>{/if}
      <div>
        <button class="open primary" disabled={!dirty || saving || !draft.name.trim() || !draft.prompt.trim()} onclick={save}>
          {saving ? 'Saving…' : isNew ? 'Create task' : 'Save changes'}
        </button>
      </div>
    </div>
  {/if}

  {#if !isNew && task}
    <div class="ptitle" style="margin-top:16px">Runs</div>
    {#if !task.runs.length}<div class="none">No runs yet — hit Run now, or wait for the schedule.</div>{/if}
    {#each task.runs as r (r.id)}
      <button class="runrow" class:unseen={TERMINAL.includes(r.status) && !r.seen} onclick={() => go('/r/' + r.id)}>
        <span class="statusicon {r.status}"><Icon name={STAT_ICON[r.status] || 'clock'} size={13} /></span>
        <span class="runwhen">{fmtStamp(r.started_at)}</span>
        <span class="runsum">{r.summary || r.error || r.status}</span>
      </button>
    {/each}
  {/if}
</div>

<style>
  .taskpage { padding: 20px 24px; overflow-y: auto; }
  .tphead { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .tpactions { display: flex; gap: 8px; flex-wrap: wrap; }
  .tpconfig { display: flex; flex-direction: column; gap: 12px; max-width: 720px; margin-top: 12px; }
  .tpconfig label { display: flex; flex-direction: column; gap: 4px; font-size: var(--text-sm); color: var(--muted); }
  .tpconfig input[type="text"], .tpconfig textarea, .tpconfig select { font: inherit; padding: 8px 10px; }
  .runrow { display: flex; gap: 10px; align-items: baseline; width: 100%; text-align: left;
            padding: 8px 10px; background: none; border: none; cursor: pointer; }
  .runrow:hover { background: var(--surface-2, rgba(128,128,128,.08)); }
  .runrow.unseen .runsum { font-weight: var(--fw-semibold); }
  .runsum { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

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
  .delconfirm { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .delconfirm .confirm { color: #d8552f; font-size: 13px; }
  .ptitle { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .taskerror { display: flex; align-items: flex-start; gap: 7px; margin-top: 8px; padding: 9px 11px; font-size: var(--text-sm); line-height: var(--leading-snug); color: var(--ag2-observer); background: color-mix(in srgb, var(--ag2-observer) 9%, transparent); border: 1px solid color-mix(in srgb, var(--ag2-observer) 28%, transparent); border-radius: var(--radius-sm); overflow-wrap: anywhere; }
  .statusicon { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 16px; color: var(--muted); }
  .statusicon.completed { color: #3ba55d; }
  .statusicon.failed { color: #d8552f; }
  .statusicon.cancelled { color: var(--muted); }
  .statusicon.running { color: var(--info); }
  .statusicon.needs_input { color: var(--accent); }
  .runwhen { flex: none; color: var(--muted); font-size: 13px; }
</style>
