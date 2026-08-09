<script lang="ts">
  // The task's page: a Cowork-style read-only view (name / description / status /
  // instructions / model / working folders / schedule / approvals) + its run history,
  // that edits ITSELF inline (ADR 0014). The pencil flips the whole page into a form;
  // Save commits every field — including folder Grants — atomically; Cancel discards
  // all. `/t/new` is the same page opened directly in edit state (single column), where
  // Save POSTs instead of PATCHes. No modal. Each run opens as a chat thread at /r/{id}.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { go, goTab, route, openOverlay } from '../../router.ts'
  import { profiles, tasks, pendingTaskEdit, SETTINGS_PAGE } from '../../store.ts'
  import { getActiveProfileId } from '../../lib/profile.ts'
  import { foldersStore, loadFolders, applyFolders } from '../../lib/folders.ts'
  import { llmConfigs, loadLlmConfigs, isUsable } from '../../lib/llm.ts'
  import { TYPE_LABEL } from '../../lib/providerLabels.ts'
  import { folderGrantDiff, scheduleValue, taskEditPatch } from '../../lib/taskEdit.ts'
  import type { FolderGrantIntent, FolderGrantState, GrantOp, ScheduleValue, TaskFolderMode } from '../../lib/taskEdit.ts'
  import { errText } from '../../lib/errors.ts'
  import { ApiError } from '../../transport/http.ts'
  import { FolderConflict, type Folder, type FsRoots, type GrantMode, type RunStatus, type Task, type TaskWithRuns } from '../../schemas/index.ts'
  import Icon from '../Icon.svelte'
  import BrandMark from '../BrandMark.svelte'
  import { MARK_SIZE } from '../ModelSwitcherView.svelte'
  import AppBar from '../AppBar.svelte'
  import AccessSwitch from '../AccessSwitch.svelte'
import WriteSwitch from '../WriteSwitch.svelte'
  import FolderPicker from '../FolderPicker.svelte'
  import ScheduleField from './ScheduleField.svelte'
  import RecallField from './RecallField.svelte'
  import { fmtStamp, fmtNextIn } from '../../lib/time.ts'

  const TERMINAL: RunStatus[] = ['completed', 'failed', 'cancelled']

  // One folder row in the read-only view: the grant reality the diff consumes, plus
  // what the row renders. The edit buffer adds the intended effective `mode`.
  type FolderStateRow = FolderGrantState & { name: string; exists: boolean }
  type FolderRow = FolderGrantIntent & { name: string; exists: boolean; taskMode?: TaskFolderMode | null }

  let task = $state<TaskWithRuns | null>(null)   // server copy (null while loading / for 'new')
  let perms = $state<string[]>([])               // this task's always-allowed command rules
  let running = $state(false)
  let pausing = $state(false)
  let confirmDel = $state(false)
  let error = $state('')

  // Edit state (ADR 0014). `editing` flips the whole page into a form; on `/t/new` it
  // is on from the moment the route loads. All field edits — scalars and folders —
  // buffer here and write to the server only on Save.
  let editing = $state(false)
  let saving = $state(false)
  let ename = $state('')
  let edesc = $state('')
  let eprompt = $state('')
  let emodel = $state<string | null>(null)
  let eschedule = $state<ScheduleValue>({ kind: 'manual', at: null, cron: null })
  let erecall = $state(0)
  let efolders = $state<FolderRow[]>([])   // intended folder set
  let pickerOpen = $state(false)
  let modelOpen = $state(false)   // model-override popover (edit mode)
  let roots = $state<Partial<FsRoots>>({})   // fs roots for the FolderPicker (cwd/home/workspace)
  let _createFoldersSeeded = false

  const isNew = $derived($route.id === 'new')
  // Whether the page shows the form. `/t/new` is always a form — driven off `isNew`
  // directly (not just the `editing` flag), so the very first render can't fall into
  // the read-only branch and dereference the still-null `task` before load() runs.
  const inEdit = $derived(editing || isNew)

  // Live Folder snapshot — mirror TaskFolders' effective-access model exactly: a task
  // reaches its own task-scope folders AND the profile folders, with a task-scope grant
  // overriding the profile mode (a task 'none' override blocks a profile folder).
  const pid = $derived($profiles.activeId || getActiveProfileId() || '')
  onMount(() => {
    if (!$foldersStore.loaded) loadFolders()
    if (!$llmConfigs.loaded) loadLlmConfigs()   // no composer here to seed the config list
    api.settings().then((s) => { roots = s.fs }).catch(() => {})
  })

  // App-bar subtitle: "Profile • Model", matching the run bar (Thread.svelte). While
  // editing, mirror the buffered model so the header stays an accurate reflection; the
  // task's own model wins, falling back to the install's active default.
  const activeProfile = $derived($profiles.list.find((p) => p.id === $profiles.activeId))
  const effModelId = $derived(inEdit ? emodel : task?.model)
  const taskModel = $derived($llmConfigs.configs.find((c) => c.id === (effModelId || $llmConfigs.active)))
  const subtitle = $derived([activeProfile?.name, taskModel?.name].filter(Boolean).join(' • '))
  // The Model row's read-only label: the task's chosen config, else "Profile default".
  const modelRowLabel = $derived.by(() => {
    const id = task?.model
    if (!id) return 'Profile default'
    return $llmConfigs.configs.find((c) => c.id === id)?.name || id
  })
  // Edit-mode picker: the buffered override config (null `emodel` = Profile default).
  const emodelConfig = $derived($llmConfigs.configs.find((c) => c.id === emodel) || null)
  // Read-only preview: the saved override config (null model = Profile default, shown as
  // plain text; a stale/unknown id falls through to modelRowLabel's raw string).
  const roModelConfig = $derived.by(() => {
    const id = task?.model
    return id ? $llmConfigs.configs.find((c) => c.id === id) || null : null
  })
  // Buffer-only: pick a config id or null (Profile default). Unlike the composer's
  // ModelSwitcher this does NOT switch the install-wide active config — it just stages
  // the task's own override for Save.
  function chooseModel(id: string | null) { emodel = id; modelOpen = false }
  function openModelSettings() { modelOpen = false; openOverlay('settings', SETTINGS_PAGE.MODELS) }

  // A task-scope grant only exists once the task does — `/t/new` has none yet.
  const tGrant = (f: Folder) => {
    const cur = task
    return cur ? f.grants.find((g) => g.profile === pid && g.task_id === cur.id && !g.chat_id) : undefined
  }
  const profileGrant = (f: Folder) => f.grants.find((g) => g.profile === pid && !g.chat_id && !g.task_id)
  const effMode = (f: Folder): GrantMode | undefined => { const t = tGrant(f); return t ? t.mode : profileGrant(f)?.mode }
  // Split into the two read-only groups (Task folders / Profile folders): task-only
  // folders that grant access, and profile folders the task can still reach (a task
  // 'none' override drops them here). Same layout, minus the controls.
  const _folders = $derived($foldersStore.folders)
  const taskFolders = $derived(!task ? [] : _folders.filter((f) => { const t = tGrant(f); return t && t.mode !== 'none' && !profileGrant(f) }))
  const profileFolders = $derived(!task ? [] : _folders.filter((f) => profileGrant(f) && effMode(f) !== 'none'))
  const hasFolders = $derived(taskFolders.length > 0 || profileFolders.length > 0)
  const modeLabel = (m: GrantMode | undefined) => (m === 'read_write' ? 'Read + write' : 'Read')

  // The task's CURRENT grant reality for this profile — every folder carrying a
  // profile- or task-scope grant, with both modes. This is the `current` side of
  // folderGrantDiff and the seed for the edit buffer.
  function currentFolderState(): FolderStateRow[] {
    if (!task) return []
    return _folders
      .filter((f) => profileGrant(f) || tGrant(f))
      .map((f) => ({
        id: f.id, path: f.path, name: f.name, exists: f.exists !== false,
        profileMode: profileGrant(f)?.mode ?? null,
        taskMode: tGrant(f)?.mode ?? null,
      }))
  }

  // The edit-buffer folder groups (task-only vs profile), derived from `efolders`.
  const eTaskFolders = $derived(efolders.filter((f) => f.profileMode == null))
  const eProfileFolders = $derived(efolders.filter((f) => f.profileMode != null))

  // Monotonic token: fast task-A → task-B navigation can let A's load() await
  // resolve after B's has started. Each call claims the next token and checks
  // it's still current before committing ANY state.
  let _loadSeq = 0
  async function load(id: string | null) {
    const seq = ++_loadSeq
    error = ''
    confirmDel = false
    editing = false
    _createFoldersSeeded = false
    if (id === 'new') { task = null; perms = []; seedBuffer(null); editing = true; return }
    // The shell only mounts this page for /t/{id}, so a missing id means there is
    // nothing to load rather than a task to report as absent.
    if (!id) { task = null; perms = []; return }
    try {
      const [t, p] = await Promise.all([api.task(id), api.taskPermissions(id).catch((): string[] => [])])
      if (seq !== _loadSeq) return
      task = t
      perms = p
      // Freshen the drawer's row from this authoritative load, so the shared store
      // and this page agree — the sync effect below then never reverts to a staler
      // list value after navigation.
      patchTaskInStore(t)
    } catch { if (seq === _loadSeq) { error = 'Task not found.'; task = null; perms = [] } }
  }
  // reload when the route's id changes
  let _lastId: string | null = ''
  $effect(() => { const id = $route.id; if (id !== _lastId) { _lastId = id; load(id) } })

  // Honour a one-shot "open Edit" request from the Drawer's task-row menu: it sets
  // pendingTaskEdit then navigates here, so enter edit state once the matching task
  // has loaded (guarded on id so a stale request can't pop Edit on the wrong task).
  // Wait for the folder snapshot before entering edit — seedBuffer captures the
  // effective grant set from it, and a Save against an empty (unloaded) seed would
  // diff every current grant to nothing and revoke it.
  $effect(() => {
    if (task && $foldersStore.loaded && $pendingTaskEdit === task.id) { pendingTaskEdit.set(null); startEdit() }
  })

  // The folder snapshot can still be loading when `/t/new` first seeds its buffer;
  // re-seed the create folder list once it arrives (before that the section is empty
  // and un-editable, so nothing the user did is lost). One-shot per create session.
  $effect(() => {
    if (isNew && $foldersStore.loaded && !_createFoldersSeeded) { _createFoldersSeeded = true; efolders = createFolderSeed() }
  })

  // Reflect drawer-side edits (rename / enable-disable) on the open page: those patch
  // the shared $tasks store, not this page's own detailed copy. Merge back only the
  // summary fields the drawer can change, so richer local data (runs, prompt, grants)
  // is preserved.
  $effect(() => {
    const cur = task
    if (!cur) return
    const row = $tasks.find((x) => x.id === cur.id)
    if (row && (row.name !== cur.name || row.paused !== cur.paused)) {
      task = { ...cur, name: row.name, paused: row.paused }
    }
  })

  // --- Edit state -----------------------------------------------------------------

  // Snapshot the task (and its effective folder set) into the local buffer. `t` may be
  // null for create. $state.snapshot, not structuredClone: `task` is a $state proxy and
  // structuredClone throws DataCloneError on proxies.
  function seedBuffer(t: TaskWithRuns | null) {
    ename = t?.name || ''
    edesc = t?.description || ''
    eprompt = t?.prompt || ''
    emodel = t?.model ?? null
    eschedule = t ? scheduleValue($state.snapshot(t.schedule)) : { kind: 'manual', at: null, cron: null }
    erecall = t?.recall_depth ?? 0
    efolders = t ? currentFolderState().map((g) => ({ ...g, mode: g.taskMode ?? g.profileMode })) : createFolderSeed()
    pickerOpen = false
    modelOpen = false
  }
  // Create has no task yet (no task-scope grants), but the profile's own folders should
  // still be editable up front — pick them at their profile mode so a change becomes a
  // task override on Save (folderGrantDiff turns mode≠profile into a set-grant against
  // the new task; an unchanged one is a no-op).
  function createFolderSeed(): FolderRow[] {
    const rows: FolderRow[] = []
    for (const f of _folders) {
      const pg = profileGrant(f)
      if (!pg) continue
      rows.push({ id: f.id, path: f.path, name: f.name, exists: f.exists !== false, profileMode: pg.mode, taskMode: null, mode: pg.mode })
    }
    return rows
  }
  function startEdit() { if (!task) return; seedBuffer(task); error = ''; editing = true }
  // Edit only — create has no Cancel (you leave a New task by navigating away, not by
  // cancelling). Discard the buffer and return to the read-only page.
  function cancel() { editing = false; error = '' }

  // Buffered folder mutations — nothing hits the server until Save. A task-only folder
  // switched Off is dropped from the buffer (removed); a profile folder switched Off
  // becomes a task `none` block (kept, so it can be re-enabled).
  function setFolderMode(entry: FolderRow, next: TaskFolderMode | null) {
    if (entry.profileMode == null) {
      if (next === null) efolders = efolders.filter((x) => x !== entry)
      else efolders = efolders.map((x) => (x === entry ? { ...x, mode: next } : x))
    } else {
      efolders = efolders.map((x) => (x === entry ? { ...x, mode: next ?? 'none' } : x))
    }
  }
  function addFolder(path: string) {
    pickerOpen = false
    if (!path || efolders.some((x) => x.path === path)) return
    efolders = [...efolders, { id: null, path, name: path, exists: true, profileMode: null, taskMode: null, mode: 'read' }]
  }

  // Apply the ordered op list from folderGrantDiff against `taskId`, resolving
  // freshly-created folders by path. 409 on create means the path is already a Folder —
  // fall back to the existing snapshot. Mirrors TaskFolders / the old modal's grant glue.
  async function applyFolderOps(ops: GrantOp[], taskId: string) {
    const byPath: Record<string, string> = {}
    for (const op of ops) {
      if (op.kind === 'create-folder') {
        let snap: { folders: Folder[] }
        let folder: Folder | undefined
        try {
          snap = await api.createFolder(op.path)
          folder = snap.folders.find((f) => f.path === op.path)
        } catch (e) {
          // 409 = the path is already registered; the body points at that Folder.
          const clash = e instanceof ApiError && e.status === 409 ? FolderConflict.safeParse(e.body) : null
          if (clash?.success) {
            snap = await api.folders()
            folder = snap.folders.find((f) => f.id === clash.data.existing.id)
          } else throw e
        }
        if (folder) { byPath[op.path] = folder.id; applyFolders(snap) }
      } else if (op.kind === 'set-grant') {
        const id = op.id ?? byPath[op.path]
        if (id) applyFolders(await api.setGrant(id, pid, op.mode, '', taskId))
      } else if (op.kind === 'revoke') {
        const id = op.id ?? byPath[op.path]
        if (id) applyFolders(await api.revokeGrant(id, pid, '', taskId))
      }
    }
  }

  async function save() {
    if (!eprompt.trim() || saving) return
    const cur = task
    if (!isNew && !cur) return    // nothing loaded yet, so nothing to PATCH
    saving = true
    error = ''
    try {
      if (isNew || !cur) {
        // Create: POST (auto-names from the prompt when name is blank), then mint the
        // buffered folders against the new id. Folder attach is best-effort — the task
        // is already saved, so a folder failure must not sink the create.
        const created = await api.createTask({
          name: ename.trim(),
          description: edesc.trim(),
          prompt: eprompt.trim(),
          model: emodel ?? '',
          schedule: $state.snapshot(eschedule),
          recall_depth: erecall,
        })
        patchTaskInStore(created)
        try { await applyFolderOps(folderGrantDiff([], $state.snapshot(efolders)), created.id) } catch { /* task saved */ }
        go('/t/' + created.id)
      } else {
        // Edit: build a minimal PATCH of changed task fields, then reconcile folders.
        const patch = taskEditPatch(cur, { name: ename, description: edesc, prompt: eprompt, model: emodel, schedule: $state.snapshot(eschedule), recall_depth: erecall })
        const updated = Object.keys(patch).length ? await api.updateTask(cur.id, patch) : cur
        task = updated
        patchTaskInStore(updated)
        // Task is saved; apply the folder diff. A folder failure surfaces without
        // corrupting the saved task (we stay in edit mode with the error shown).
        await applyFolderOps(folderGrantDiff(currentFolderState(), $state.snapshot(efolders)), cur.id)
        editing = false
      }
    } catch (e) { error = errText(e, 'save failed') }
    finally { saving = false }
  }

  async function runNow() {
    if (running || !task) return
    running = true
    try { const run = await api.runTask(task.id); go('/r/' + run.id) }
    catch (e) { error = errText(e, 'run failed') } finally { running = false }
  }

  // Patch the shared task-list store so the drawer's paused glyph (and any other
  // $tasks reader) reflects the toggle immediately. The server copy is authoritative.
  function patchTaskInStore(t: Task) {
    tasks.update((list) => list.map((x) => (x.id === t.id ? t : x)))
  }

  async function togglePause() {
    const cur = task
    if (!cur || pausing) return
    pausing = true
    try { task = await api.updateTask(cur.id, { paused: !cur.paused }); patchTaskInStore(task) }
    catch (e) { error = errText(e, 'pause failed') } finally { pausing = false }
  }

  async function del() {
    if (!task) return
    try {
      const id = task.id
      await api.deleteTask(id)
      tasks.update((list) => list.filter((x) => x.id !== id))
      goTab('tasks')
    } catch (e) { error = errText(e) }
  }

  async function revoke(rule: string) {
    const cur = task
    if (!cur) return
    try { await api.deleteTaskPermission(cur.id, rule); perms = await api.taskPermissions(cur.id) }
    catch (e) { error = errText(e, 'revoke failed') }
  }

  // Read-only prose for recall_depth, mirroring how Repeats reads schedule_desc.
  const recallLabel = (d: number) => (d === 0 ? '—' : d < 0 ? 'All previous runs' : `Last ${d} runs`)

  // Status → icon, matching Drawer.svelte's status-glyph conventions.
  const STAT_ICON: Record<RunStatus, string> = { running: 'spinner', needs_input: 'help-circle', completed: 'check', failed: 'x', cancelled: 'slash' }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') modelOpen = false }} />

<AppBar
  back={{ label: 'Tasks', onClick: () => goTab('tasks') }}
  title={inEdit ? (ename || (isNew ? 'New task' : task?.name) || 'Task') : (task?.name || 'Task')}
  {subtitle} />

<div class="thread taskpage">
  <div class="inner">
    {#if error}<div class="taskerror"><Icon name="x" size={13} /> {error}</div>{/if}

    {#if task || isNew}
      <div class="tphead">
        <div class="tpheadmain">
          {#if inEdit}
            <input class="tpnameinput" bind:value={ename} placeholder="Name — generated from the prompt if blank" />
            <input class="tpdescinput" bind:value={edesc} placeholder="Description (optional)" />
          {:else if task}
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
          {/if}
        </div>
        <div class="tpactions">
          {#if inEdit}
            {#if !isNew}
              <button class="open" disabled={saving} onclick={cancel}>Cancel</button>
            {/if}
            <button class="open primary" disabled={!eprompt.trim() || saving} onclick={save}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          {:else}
            <button class="iconbtn" title="Edit" onclick={startEdit}><Icon name="pencil" size={15} /></button>
            <span class="delwrap">
              <button class="iconbtn" title="Delete" onclick={() => (confirmDel = !confirmDel)}><Icon name="trash" size={15} /></button>
              {#if confirmDel}
                <span class="delconfirm">
                  <span class="confirm">Delete permanently?</span>
                  <button class="open danger" onclick={del}>Yes, delete</button>
                  <button class="open" onclick={() => (confirmDel = false)}>Cancel</button>
                </span>
              {/if}
            </span>
            <button class="open primary" disabled={running} onclick={runNow}>
              <Icon name="play" size={14} /> {running ? 'Starting…' : 'Run now'}
            </button>
          {/if}
        </div>
      </div>

      <div class="tpcols" class:single={isNew}>
        {#if !isNew && task}
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
        {/if}
        <section>
          <h2>Instructions</h2>
          {#if inEdit}
            <textarea class="tpinput tpprompt-input" rows="6" bind:value={eprompt}
              placeholder="What should the agent do on every run? Be specific — it runs unattended."></textarea>
          {:else if task}
            <p class="tpprompt">{task.prompt}</p>
          {/if}

          <h2>Model</h2>
          {#if inEdit}
            <!-- Same popover vocabulary as the composer's ModelSwitcher (.modelsw-*,
                 app.css) so the two model pickers read as one control. The menu drops
                 DOWN here (.tpmodel override) since the row sits mid-page, and the extra
                 "Profile default" row maps to a null override. -->
            <div class="modelsw tpmodel">
              <div class="modelsw-wrap">
                <button class="modelsw-btn" onclick={() => (modelOpen = !modelOpen)}
                        aria-haspopup="menu" aria-expanded={modelOpen} title="Model for this task">
                  {#if emodelConfig}
                    <BrandMark brand={emodelConfig.type} size={MARK_SIZE} />
                    <span class="modelsw-name">{emodelConfig.name}</span>
                    <span class="modelsw-dot" class:warn={!isUsable(emodelConfig)}></span>
                  {:else}
                    <span class="modelsw-name">{emodel ? emodel : 'Profile default'}</span>
                  {/if}
                  <Icon name="chevron-down" size={13} />
                </button>

                {#if modelOpen}
                  <button class="modelsw-scrim" aria-label="Close model menu" onclick={() => (modelOpen = false)}></button>
                  <div class="modelsw-menu" role="menu">
                    <button class="modelsw-item" class:active={emodel == null} role="menuitem" onclick={() => chooseModel(null)}>
                      <span class="modelsw-itemmeta">
                        <span class="modelsw-name">
                          Profile default{#if emodel == null}<Icon name="check" size={12} />{/if}
                        </span>
                        <span class="modelsw-sub">Use the profile's active model</span>
                      </span>
                    </button>
                    {#each $llmConfigs.configs as c (c.id)}
                      <button
                        class="modelsw-item" class:active={c.id === emodel}
                        role="menuitem"
                        disabled={!isUsable(c)}
                        title={isUsable(c) ? '' : 'Not ready — add a key or sign in via Settings'}
                        onclick={() => chooseModel(c.id)}
                      >
                        <BrandMark brand={c.type} size={MARK_SIZE} />
                        <span class="modelsw-itemmeta">
                          <span class="modelsw-name">
                            {c.name}{#if c.id === emodel}<Icon name="check" size={12} />{/if}
                          </span>
                          <span class="modelsw-sub">{TYPE_LABEL[c.type]} · {c.model}</span>
                        </span>
                        <span class="modelsw-dot" class:warn={!isUsable(c)}></span>
                      </button>
                    {/each}
                    <button class="modelsw-manage" role="menuitem" onclick={openModelSettings}>Manage models…</button>
                  </div>
                {/if}
              </div>
            </div>
          {:else}
            <!-- Read-only: the same logo + name + health-dot vocabulary as the picker
                 trigger, but a static (unclickable) row — the model reads as one control
                 across view and edit. A null model / stale id shows plain text. -->
            <div class="tpmodel-view">
              {#if roModelConfig}
                <BrandMark brand={roModelConfig.type} size={MARK_SIZE} />
                <span class="modelsw-name">{roModelConfig.name}</span>
                <span class="modelsw-dot" class:warn={!isUsable(roModelConfig)}></span>
              {:else}
                <span class="modelsw-name">{modelRowLabel}</span>
              {/if}
            </div>
          {/if}

          <h2>Folders</h2>
          {#if inEdit}
            {#if eTaskFolders.length}
              <div class="fsec">Task folders</div>
              {#each eTaskFolders as f (f.path)}
                <div class="frow">
                  <span class="fico"><Icon name="folder" size={14} /></span>
                  <span class="fname" title={f.path}>{f.name}</span>
                  <!-- Task-only folders can't be "Off" (that's removal), so they get a
                       2-position Read/Read+write toggle + an explicit Delete, not the
                       3-position switch profile folders use. Mirrors TaskFolders. -->
                  <div class="fctl">
                    <WriteSwitch mode={f.mode} onchange={(m) => setFolderMode(f, m)} />
                    <button class="iconbtn" title="Remove folder" aria-label="Remove folder" onclick={() => setFolderMode(f, null)}><Icon name="trash" size={14} /></button>
                  </div>
                </div>
              {/each}
            {/if}
            {#if eProfileFolders.length}
              <div class="fsec">Profile folders</div>
              {#each eProfileFolders as f (f.path)}
                <div class="frow">
                  <span class="fico"><Icon name="folder" size={14} /></span>
                  <span class="fname" title={f.path}>{f.name}</span>
                  <AccessSwitch mode={f.mode} onchange={(m) => setFolderMode(f, m)} />
                </div>
              {/each}
            {/if}
            <button class="open addfolder" onclick={() => (pickerOpen = !pickerOpen)}>
              <Icon name="folder" size={14} /> Add working folder
            </button>
            {#if pickerOpen}
              <div class="tppicker">
                <FolderPicker {roots} start={roots.cwd || roots.home || ''} onUse={addFolder} />
              </div>
            {/if}
          {:else if hasFolders}
            {#if taskFolders.length}
              <div class="fsec">Task folders</div>
              {#each taskFolders as f (f.id)}
                <div class="frow">
                  <span class="fico"><Icon name="folder" size={14} /></span>
                  <span class="fname" title={f.path}>{f.name}{f.exists === false ? ' — path is missing' : ''}</span>
                  <span class="fmode">{modeLabel(effMode(f))}</span>
                </div>
              {/each}
            {/if}
            {#if profileFolders.length}
              <div class="fsec">Profile folders</div>
              {#each profileFolders as f (f.id)}
                <div class="frow">
                  <span class="fico"><Icon name="folder" size={14} /></span>
                  <span class="fname" title={f.path}>{f.name}{f.exists === false ? ' — path is missing' : ''}</span>
                  <span class="fmode">{modeLabel(effMode(f))}</span>
                </div>
              {/each}
            {/if}
          {:else}
            <p class="tpmeta">—</p>
          {/if}

          <h2>Repeats</h2>
          {#if inEdit}
            <ScheduleField bind:schedule={eschedule} />
          {:else if task}
            <p class="tpmeta">{task.schedule_desc}</p>
          {/if}

          <h2>Earlier runs</h2>
          {#if inEdit}
            <RecallField bind:depth={erecall} />
          {:else if task}
            <p class="tpmeta">{recallLabel(task.recall_depth)}</p>
          {/if}

          {#if !isNew}
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
          {/if}
        </section>
      </div>
    {:else if !isNew}
      <div class="none">Loading…</div>
    {/if}
  </div>
</div>

<style>
  /* Reuses the .thread/.inner shell (Thread.svelte) so the page reads as the same
     scrolling column with the same max-width/centering as the chat/run thread,
     instead of a bespoke full-bleed panel. */
  .taskpage { padding: 28px 0 60px; overflow-y: auto; }

  .tphead { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .tpheadmain { flex: 1; min-width: 0; }
  .tphead h1 { margin: 0 0 4px; font-size: var(--text-2xl); color: var(--ink); }
  .tpdesc { color: var(--muted); font-size: 13px; margin-bottom: 8px; max-width: 60ch; }
  .tpstatus { display: flex; align-items: center; gap: 10px; }
  .tpactions { display: flex; align-items: center; gap: 8px; flex: none; }

  /* Edit-mode name/description live where the heading was (spec story 4) — the name
     input carries the h1's size/weight so the swap is in-place, not a form pop-in. */
  .tpnameinput, .tpdescinput { display: block; width: 100%; font: inherit; color: var(--ink);
    border: 1px solid var(--line); border-radius: 8px; padding: 6px 9px; background: var(--bg); }
  .tpnameinput { font-size: var(--text-2xl); font-weight: var(--fw-semibold); margin-bottom: 8px; }
  .tpdescinput { font-size: 13px; color: var(--muted); max-width: 60ch; }
  .tpnameinput:focus, .tpdescinput:focus, .tpinput:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }

  /* Shared shape for the edit-mode field controls (prompt textarea, model select). */
  .tpinput { width: 100%; box-sizing: border-box; font: inherit; font-size: 13px; color: var(--ink);
    border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px; background: var(--bg); }
  .tpprompt-input { line-height: 1.5; resize: vertical; }

  /* The Model picker reuses the composer's .modelsw-* popover (app.css) verbatim. Two
     deltas for this context: the trigger aligns to the left edge like the other field
     controls (not the composer's centered pill), and the menu drops DOWN — the composer's
     rule opens it UPWARD because it's pinned to the screen bottom, but this row is mid-page. */
  .tpmodel { display: flex; }
  /* Pull the pill left by its own horizontal padding so the logo lines up flush with the
     column (and the read-only row) — without stripping the padding, which would make the
     content hug the pill's rounded edge once the hover/active fill shows. */
  .tpmodel .modelsw-btn { margin-left: -9px; }
  .tpmodel :global(.modelsw-menu) { top: calc(100% + 6px); bottom: auto; }
  /* Read-only mirror of the picker trigger: same logo/name/dot row, but static — no
     pill background, hover, or pointer. Sits flush-left like the other .tpmeta values. */
  .tpmodel-view { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink); }

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

  .tpcols { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 32px; margin-top: 24px; align-items: start; }
  /* Create (`/t/new`): the task does not exist yet, so no History / Always-allowed —
     a single centered column keeps the focus on the fields being filled (spec story 15). */
  .tpcols.single { grid-template-columns: minmax(0, 1fr); max-width: 640px; }
  @media (max-width: 760px) { .tpcols { grid-template-columns: 1fr; } }
  .tpcols h2 { margin: 20px 0 8px; font-size: 12px; font-weight: var(--fw-semibold); color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
  .tpcols section > h2:first-child { margin-top: 0; }
  .tpprompt { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.5; color: var(--ink); margin: 0; }
  .tpmeta { overflow-wrap: anywhere; font-size: 13px; color: var(--ink); margin: 0; }

  /* Folder rows mirror TaskFolders.svelte's .cfsec/.cfrow layout so the preview and
     the edit form read as one surface — read-only shows a muted mode label; edit mode
     swaps in an AccessSwitch. The section header breaks .frow adjacency, so the last
     row of each group carries no trailing divider. */
  .fsec { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin: 14px 0 4px; }
  .fsec:first-of-type { margin-top: 0; }
  .frow { display: flex; align-items: center; gap: 10px; padding: 7px 0; }
  .frow + .frow { border-top: 1px solid var(--line); }
  .fico { flex: none; display: inline-flex; color: var(--muted); }
  .fname { flex: 1; min-width: 0; font-size: 13px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fmode { flex: none; font-size: 13px; color: var(--muted); }

  /* Task-folder edit control: the WriteSwitch (Read / Read+write) + a trash button. */
  .fctl { flex: none; display: inline-flex; align-items: center; gap: 10px; }

  .addfolder { margin-top: 12px; }
  .tppicker { margin-top: 10px; border: 1px solid var(--line); border-radius: var(--radius-md); padding: 10px; background: var(--surface-sunk, var(--bg)); }

  .permrow { display: flex; align-items: center; gap: 8px; border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 6px 10px; margin-bottom: 6px; }
  .permrow code { flex: 1; min-width: 0; font-family: var(--mono); font-size: 12px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* .iconbtn (Edit / Delete / revoke) and the .open button family are the app-wide
     canonical rules (app.css) — .open is scoped to .taskpage there, .iconbtn is a
     bare global. Nothing to reproduce here. */

  /* Run rows: a bordered card that fills and brightens its border on hover, with the
     .drow.unseen accent-tint treatment for runs the user hasn't opened yet. */
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

  /* Delete confirm is a popover anchored under the trash button (not inline in
     .tpactions) so opening it never widens the header row / shifts the layout. */
  .delwrap { position: relative; display: inline-flex; flex: none; }
  .delconfirm {
    position: absolute; top: calc(100% + 8px); right: 0; z-index: 5;
    display: flex; align-items: center; gap: 8px; white-space: nowrap;
    padding: 8px 10px; background: var(--surface-elevated); border: 1px solid var(--line);
    border-radius: var(--radius-sm); box-shadow: var(--shadow-lg);
  }
  .delconfirm .confirm { color: var(--danger); font-size: 13px; }
  .taskerror { display: flex; align-items: flex-start; gap: 7px; margin-bottom: 8px; padding: 9px 11px; font-size: var(--text-sm); line-height: var(--leading-snug); color: var(--ag2-observer); background: color-mix(in srgb, var(--ag2-observer) 9%, transparent); border: 1px solid color-mix(in srgb, var(--ag2-observer) 28%, transparent); border-radius: var(--radius-sm); overflow-wrap: anywhere; }
  .statusicon { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 16px; color: var(--muted); }
  .statusicon.completed { color: var(--success); }
  .statusicon.failed { color: var(--danger); }
  .statusicon.cancelled { color: var(--muted); }
  .statusicon.running { color: var(--info); }
  .statusicon.needs_input { color: var(--accent); }
  .runwhen { flex: none; color: var(--muted); font-size: 13px; }
</style>
