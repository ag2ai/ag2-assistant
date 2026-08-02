<script>
  // Folder access for ONE task — mirrors ChatFolders, one level up: the task's
  // own folders (task-scope Grants) + profile folders with a per-TASK override
  // (Read / Read+write / Off writes a task-scope Grant; Off blocks the folder
  // for every run of this task without touching the profile Grant). Task folders
  // (task-only) get the same 2-position iOS switch as ChatFolders' chat folders,
  // with Delete + Move to profile behind a kebab. The shared snapshot means edits
  // here refresh the composer chips + Settings + TaskPage live.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { profiles } from '../../store.js'
  import { foldersStore, loadFolders, applyFolders } from '../../lib/folders.js'
  import { getActiveProfileId } from '../../lib/profile.js'
  import Icon from '../Icon.svelte'
  import AccessSwitch from '../AccessSwitch.svelte'
  import WriteSwitch from '../WriteSwitch.svelte'
  import FolderPicker from '../FolderPicker.svelte'

  let { taskId } = $props()

  let busy = $state(false)
  let err = $state('')
  let note = $state('')
  let menuFor = $state('')     // id of the task folder whose overflow menu is open
  let pickerOpen = $state(false)
  let roots = $state({})
  const pid = $derived($profiles.activeId || getActiveProfileId())
  const folders = $derived($foldersStore.folders)

  onMount(() => {
    if (!$foldersStore.loaded) loadFolders()
    api.settings().then((s) => { roots = s.fs || {} }).catch(() => {})
  })
  async function run(fn) {
    err = ''; busy = true
    try { applyFolders(await fn()) } catch (e) { err = String(e.message || e) }
    busy = false
  }

  const tGrant = (f) => (f.grants || []).find((g) => g.profile === pid && g.task_id === taskId && !g.chat_id)
  const profileGrant = (f) => (f.grants || []).find((g) => g.profile === pid && !g.chat_id && !g.task_id)
  const profileFolders = $derived(folders.filter((f) => profileGrant(f)))
  const taskFolders = $derived(folders.filter((f) => tGrant(f) && !profileGrant(f)))
  const effMode = (f) => { const t = tGrant(f); return t ? t.mode : profileGrant(f)?.mode }

  // Task-only folders: null mode = revoke this task's grant.
  function setTaskMode(f, mode) {
    const cur = tGrant(f)
    if (!mode) { if (cur) run(() => api.revokeGrant(f.id, pid, '', taskId)); return }
    if (cur?.mode === mode) return
    run(() => api.setGrant(f.id, pid, mode, '', taskId))
  }
  // Profile folders: write a task-scoped OVERRIDE (this task only). null/'none'
  // blocks; setting it back to the profile mode drops the override.
  function setTaskOverride(f, mode) {
    const target = mode || 'none'
    if (effMode(f) === target) return
    const cur = tGrant(f)
    if (target === profileGrant(f)?.mode) { if (cur) run(() => api.revokeGrant(f.id, pid, '', taskId)); return }
    run(() => api.setGrant(f.id, pid, target, '', taskId))
  }
  // Promote a task-only folder to the profile (reachable from every chat): mint a
  // profile-scope grant at the same mode, then drop the now-redundant task grant.
  function moveToProfile(f) {
    const cur = tGrant(f)
    if (!cur) return
    const mode = cur.mode === 'read_write' ? 'read_write' : 'read'
    run(async () => {
      await api.setGrant(f.id, pid, mode)          // profile scope (no chat/task)
      return api.revokeGrant(f.id, pid, '', taskId)
    })
  }

  // Pick a path → mint (or reuse on 409) the Folder → grant this task read, unless
  // the profile already covers it (then just say so).
  const addFolder = (path) => {
    note = ''
    run(async () => {
      let snap, folder
      try {
        snap = await api.createFolder(path)
        folder = (snap.folders || []).find((f) => f.path === path)
      } catch (e) {
        if (e.status === 409 && e.body?.existing?.id) {
          snap = await api.folders()
          folder = (snap.folders || []).find((f) => f.id === e.body.existing.id)
        } else throw e
      }
      if (!folder) throw new Error('Could not resolve that folder')
      const pg = profileGrant(folder)
      const t = tGrant(folder)
      if (pg) {
        // The profile already reaches every run — a task 'none' override blocks it, drop that.
        if (t && t.mode === 'none') { snap = await api.revokeGrant(folder.id, pid, '', taskId); note = `"${folder.name}" is available to this task again.` }
        else note = `"${folder.name}" is already available from the profile.`
      } else if (t) {
        note = `"${folder.name}" is already in this task.`
      } else {
        snap = await api.setGrant(folder.id, pid, 'read', '', taskId)
      }
      pickerOpen = false
      return snap
    })
  }
</script>

<div class="tf">
  <p class="muted hint">Task folders are shared across every run of this task. Profile folders reach the whole profile — changing one here overrides it for this task only, leaving other tasks and chats untouched.</p>
  {#if err}<p class="muted errline">{err}</p>{/if}
  {#if note}<p class="muted noteline">{note}</p>{/if}

  {#if taskFolders.length}
    <div class="cfsec">Task folders</div>
    {#each taskFolders as f (f.id)}
      {@const tg = tGrant(f)}
      <div class="cfrow">
        <span class="cfico"><Icon name="folder" size={14} /></span>
        <span class="cfname" title={f.path}>{f.name}</span>
        <div class="cfctl">
          <WriteSwitch mode={tg?.mode} disabled={busy} onchange={(m) => setTaskMode(f, m)} />
          <span class="cfmenuwrap">
            <button class="cfkebab" aria-label="More actions" aria-expanded={menuFor === f.id} disabled={busy} onclick={() => (menuFor = menuFor === f.id ? '' : f.id)}>⋯</button>
            {#if menuFor === f.id}
              <!-- Scrim: closing on an outside click duplicates the kebab toggle,
                   so it stays out of the a11y tree. -->
              <div class="cfscrim" role="presentation" onclick={() => (menuFor = '')}></div>
              <div class="cfmenu">
                <button onclick={() => { menuFor = ''; moveToProfile(f) }}><Icon name="users" size={14} /> Move to profile</button>
                <button class="danger" onclick={() => { menuFor = ''; setTaskMode(f, null) }}><Icon name="trash" size={14} /> Delete</button>
              </div>
            {/if}
          </span>
        </div>
      </div>
    {/each}
  {/if}

  {#if profileFolders.length}
    <div class="cfsec">Profile folders</div>
    {#each profileFolders as f (f.id)}
      <div class="cfrow">
        <span class="cfico"><Icon name="folder" size={14} /></span>
        <span class="cfname" title={f.path}>{f.name}</span>
        <AccessSwitch mode={effMode(f)} disabled={busy} onchange={(m) => setTaskOverride(f, m)} />
      </div>
    {/each}
  {/if}

  <div class="tfadd">
    <button class="open" onclick={() => { pickerOpen = !pickerOpen; note = '' }}>
      <Icon name="folder" size={14} /> Add folder
    </button>
  </div>
  {#if pickerOpen}
    <div class="tfpicker">
      <FolderPicker {roots} start={roots.cwd || roots.home || ''} busy={busy} onUse={addFolder} />
    </div>
  {/if}
</div>

<style>
  .tf { display: flex; flex-direction: column; }
  .hint { font-size: 12px; margin: 0 0 10px; }
  .errline { color: var(--danger); font-size: 13px; }
  .noteline { font-size: 13px; }
  .cfsec { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin: 16px 0 6px; }
  .cfrow { display: flex; align-items: center; gap: 10px; padding: 8px 0; }
  /* Divider only between adjacent folders — the section header breaks adjacency,
     so the last row of each section has no trailing border. */
  .cfrow + .cfrow { border-top: 1px solid var(--line); }
  .cfico { flex: none; display: inline-flex; color: var(--text-muted); }
  .cfname { flex: 1; min-width: 0; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cfctl { flex: none; display: inline-flex; align-items: center; gap: 10px; }

  /* Overflow menu — Move to profile / Delete tucked behind a quiet kebab. */
  .cfmenuwrap { position: relative; flex: none; }
  .cfkebab { background: none; border: none; cursor: pointer; color: var(--text-muted); font-size: 17px; line-height: 1; padding: 2px 7px; border-radius: 6px; }
  .cfkebab:hover { color: var(--ink); background: var(--code); }
  .cfkebab:disabled { cursor: default; }
  .cfscrim { position: fixed; inset: 0; z-index: 40; }
  .cfmenu { position: absolute; right: 0; top: 100%; margin-top: 5px; z-index: 41; min-width: 170px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 10px 28px rgba(0, 0, 0, .28); padding: 5px; display: flex; flex-direction: column; gap: 2px; }
  .cfmenu button { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; background: none; border: none; padding: 8px 10px; font: inherit; font-size: 13px; color: var(--ink); cursor: pointer; border-radius: 7px; }
  .cfmenu button:hover { background: var(--code); }
  .cfmenu button.danger { color: var(--danger); }

  .tfadd { margin-top: 14px; }
  .tfpicker { margin-top: 10px; border: 1px solid var(--line); border-radius: var(--radius-md); padding: 10px; background: var(--surface-sunk, var(--bg)); }

  /* .open buttons here are styled by the app-wide rule (app.css), which scopes
     `.open` to this component's `.tf` root. Nothing to reproduce locally. */
</style>
