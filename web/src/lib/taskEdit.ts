// Pure logic for the inline Task editor (ADR 0014): the folder-grant reconciliation
// and the edit PATCH builder. Import-free so it unit-tests under node:test; TaskPage
// calls these and issues the resulting `api` calls, holding no diff logic of its own.
//
// Folder model (mirrors TaskFolders / TaskPage): for this task's profile a folder can
// carry a PROFILE-scope grant (reaches every run) and/or a TASK-scope grant that
// OVERRIDES the profile mode for this task — including a task `none` grant that blocks
// a profile folder. The task's *effective* mode is the task grant when present, else
// the profile grant. Editing buffers an intended effective mode per folder; on Save we
// diff it against the current grant reality and replay create/set-grant/revoke ops.

const REAL = (m) => m === 'read' || m === 'read_write'

// The TASK-scope grant a folder should end up with, given its profile-scope mode
// (null = no profile grant behind it) and the desired effective `mode`:
//   task-only folder  → the real mode itself, or null (revoke) when off/blocked.
//   profile folder    → no override when the desired mode equals the profile mode
//                       (or is unset); otherwise a task override at that mode,
//                       where `none` is the block.
function desiredTaskGrant(profileMode, mode) {
  if (!profileMode) return REAL(mode) ? mode : null
  if (!mode || mode === profileMode) return null
  return mode
}

// Reconcile the task's CURRENT grant state to the buffered INTENDED effective set.
//
// Entry shape (both lists), keyed by folder identity:
//   current  : { id, path, profileMode, taskMode }  — actual grant reality for this
//              task's profile. profileMode/taskMode are 'read'|'read_write'|'none'|null.
//   intended : { id, path, profileMode, mode }       — desired effective mode. `id` is
//              null when the folder must be created before it can be granted.
//
// Returns an ordered op list the component applies via the folder APIs:
//   { kind: 'create-folder', path }              → api.createFolder(path)
//   { kind: 'set-grant', id, path, mode }        → api.setGrant(id, pid, mode, '', taskId)
//   { kind: 'revoke', id, path }                 → api.revokeGrant(id, pid, '', taskId)
// A create-folder always precedes the set-grant for the same new path (id resolved
// from the create response by path). Folders present before but absent from `intended`
// revert to their profile baseline (their task grant, if any, is revoked).
export function folderGrantDiff(current, intended) {
  const cur = current || []
  const want = intended || []
  const ops = []
  const curById = new Map(cur.filter((e) => e.id != null).map((e) => [e.id, e]))
  const curByPath = new Map(cur.map((e) => [e.path, e]))
  const matched = new Set()

  for (const w of want) {
    const c = w.id != null ? curById.get(w.id) : curByPath.get(w.path)
    if (c) matched.add(c)
    const profileMode = w.profileMode ?? c?.profileMode ?? null
    const curTask = c?.taskMode ?? null
    const dtg = desiredTaskGrant(profileMode, w.mode)
    if (dtg === curTask) continue
    if (dtg === null) {
      ops.push({ kind: 'revoke', id: w.id ?? c?.id ?? null, path: w.path })
      continue
    }
    if (w.id == null && !c) ops.push({ kind: 'create-folder', path: w.path })
    ops.push({ kind: 'set-grant', id: w.id ?? c?.id ?? null, path: w.path, mode: dtg })
  }

  // Folders that had a task grant but are no longer in the intended set: drop the
  // override / task grant, reverting to the profile baseline (removing a task folder).
  for (const c of cur) {
    if (matched.has(c)) continue
    if (c.taskMode != null) ops.push({ kind: 'revoke', id: c.id, path: c.path })
  }
  return ops
}

const scheduleEqual = (a, b) => {
  const n = (s) => ({ kind: s?.kind ?? 'manual', at: s?.at ?? null, cron: s?.cron ?? null })
  const x = n(a)
  const y = n(b)
  return x.kind === y.kind && x.at === y.at && x.cron === y.cron
}

// Minimal PATCH body for an edit Save: only fields that actually changed. A blank name
// is omitted (blank name leaves the existing name alone — auto-naming is create-only);
// description/prompt are trimmed; model normalises null → '' (profile default). An
// unchanged task yields an empty object.
export function taskEditPatch(initial, buffer) {
  const patch = {}
  const name = (buffer.name || '').trim()
  if (name && name !== (initial?.name || '')) patch.name = name

  const description = (buffer.description || '').trim()
  if (description !== (initial?.description || '')) patch.description = description

  const prompt = (buffer.prompt || '').trim()
  if (prompt !== (initial?.prompt || '')) patch.prompt = prompt

  const model = buffer.model ?? ''
  if (model !== (initial?.model ?? '')) patch.model = model

  if (!scheduleEqual(initial?.schedule, buffer.schedule)) patch.schedule = buffer.schedule

  return patch
}
