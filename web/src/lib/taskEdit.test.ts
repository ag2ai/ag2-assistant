import { test } from 'node:test'
import assert from 'node:assert/strict'
import { folderGrantDiff, scheduleValue, taskEditPatch } from './taskEdit.ts'
import type { FolderGrantIntent, FolderGrantState } from './taskEdit.ts'

// folderGrantDiff — current/intended entries are keyed by folder identity and carry
// the profile-scope mode behind them; ops are tagged {kind, ...}. See module header.

test('folderGrantDiff: no-op when intended equals current', () => {
  const current: FolderGrantState[] = [
    { id: 'f1', path: '/a', profileMode: null, taskMode: 'read' },        // task-only
    { id: 'f2', path: '/b', profileMode: 'read', taskMode: null },        // profile folder, no override
  ]
  const intended: FolderGrantIntent[] = [
    { id: 'f1', path: '/a', profileMode: null, mode: 'read' },
    { id: 'f2', path: '/b', profileMode: 'read', mode: 'read' },
  ]
  assert.deepEqual(folderGrantDiff(current, intended), [])
})

test('folderGrantDiff: add a new task folder that already exists as a Folder', () => {
  const current: FolderGrantState[] = []
  const intended: FolderGrantIntent[] = [{ id: 'f9', path: '/new', profileMode: null, mode: 'read' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'set-grant', id: 'f9', path: '/new', mode: 'read' },
  ])
})

test('folderGrantDiff: add a folder that must be created before granting', () => {
  const current: FolderGrantState[] = []
  const intended: FolderGrantIntent[] = [{ id: null, path: '/fresh', profileMode: null, mode: 'read_write' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'create-folder', path: '/fresh' },
    { kind: 'set-grant', id: null, path: '/fresh', mode: 'read_write' },
  ])
})

test('folderGrantDiff: remove a task folder (dropped from intended) → revoke', () => {
  const current: FolderGrantState[] = [{ id: 'f1', path: '/a', profileMode: null, taskMode: 'read' }]
  assert.deepEqual(folderGrantDiff(current, []), [
    { kind: 'revoke', id: 'f1', path: '/a' },
  ])
})

test('folderGrantDiff: task folder set to Off (mode null/none) → revoke', () => {
  const current: FolderGrantState[] = [{ id: 'f1', path: '/a', profileMode: null, taskMode: 'read' }]
  const intended: FolderGrantIntent[] = [{ id: 'f1', path: '/a', profileMode: null, mode: 'none' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'revoke', id: 'f1', path: '/a' },
  ])
})

test('folderGrantDiff: change a task folder mode read → read_write', () => {
  const current: FolderGrantState[] = [{ id: 'f1', path: '/a', profileMode: null, taskMode: 'read' }]
  const intended: FolderGrantIntent[] = [{ id: 'f1', path: '/a', profileMode: null, mode: 'read_write' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'set-grant', id: 'f1', path: '/a', mode: 'read_write' },
  ])
})

test('folderGrantDiff: change a task folder mode read_write → read', () => {
  const current: FolderGrantState[] = [{ id: 'f1', path: '/a', profileMode: null, taskMode: 'read_write' }]
  const intended: FolderGrantIntent[] = [{ id: 'f1', path: '/a', profileMode: null, mode: 'read' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'set-grant', id: 'f1', path: '/a', mode: 'read' },
  ])
})

test('folderGrantDiff: block a profile folder via a task `none` override', () => {
  const current: FolderGrantState[] = [{ id: 'f2', path: '/b', profileMode: 'read', taskMode: null }]
  const intended: FolderGrantIntent[] = [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'none' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'set-grant', id: 'f2', path: '/b', mode: 'none' },
  ])
})

test('folderGrantDiff: widen a profile folder via a task read_write override', () => {
  const current: FolderGrantState[] = [{ id: 'f2', path: '/b', profileMode: 'read', taskMode: null }]
  const intended: FolderGrantIntent[] = [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'read_write' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'set-grant', id: 'f2', path: '/b', mode: 'read_write' },
  ])
})

test('folderGrantDiff: profile folder back to profile mode → revoke the override', () => {
  const current: FolderGrantState[] = [{ id: 'f2', path: '/b', profileMode: 'read', taskMode: 'read_write' }]
  const intended: FolderGrantIntent[] = [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'read' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'revoke', id: 'f2', path: '/b' },
  ])
})

test('folderGrantDiff: unblock a task-none profile folder → revoke the block', () => {
  const current: FolderGrantState[] = [{ id: 'f2', path: '/b', profileMode: 'read', taskMode: 'none' }]
  const intended: FolderGrantIntent[] = [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'read' }]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'revoke', id: 'f2', path: '/b' },
  ])
})

test('folderGrantDiff: re-mode + block in one Save, ordered per intended', () => {
  const current: FolderGrantState[] = [
    { id: 'f1', path: '/a', profileMode: null, taskMode: 'read' },   // task-only, widen
    { id: 'f2', path: '/b', profileMode: 'read', taskMode: null },   // profile, block
    { id: 'f3', path: '/c', profileMode: null, taskMode: 'read' },   // task-only, remove
  ]
  const intended: FolderGrantIntent[] = [
    { id: 'f1', path: '/a', profileMode: null, mode: 'read_write' },
    { id: 'f2', path: '/b', profileMode: 'read', mode: 'none' },
  ]
  assert.deepEqual(folderGrantDiff(current, intended), [
    { kind: 'set-grant', id: 'f1', path: '/a', mode: 'read_write' },
    { kind: 'set-grant', id: 'f2', path: '/b', mode: 'none' },
    { kind: 'revoke', id: 'f3', path: '/c' },
  ])
})

test('folderGrantDiff: create — an unchanged profile folder yields no op (no task grant)', () => {
  // Create seeds profile folders at their profile mode against an empty `current`.
  const intended: FolderGrantIntent[] = [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'read' }]
  assert.deepEqual(folderGrantDiff([], intended), [])
})

test('folderGrantDiff: create — a re-moded/blocked profile folder becomes a task override', () => {
  assert.deepEqual(folderGrantDiff([], [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'read_write' }]), [
    { kind: 'set-grant', id: 'f2', path: '/b', mode: 'read_write' },
  ])
  assert.deepEqual(folderGrantDiff([], [{ id: 'f2', path: '/b', profileMode: 'read', mode: 'none' }]), [
    { kind: 'set-grant', id: 'f2', path: '/b', mode: 'none' },
  ])
})

test('folderGrantDiff: empty / missing input is safe', () => {
  assert.deepEqual(folderGrantDiff(undefined, undefined), [])
  assert.deepEqual(folderGrantDiff([], []), [])
})

// taskEditPatch — minimal changed-fields PATCH for an edit Save.

const base = {
  name: 'Nightly',
  description: 'runs at night',
  prompt: 'do the thing',
  model: 'gpt-x',
  schedule: { kind: 'cron', at: null, cron: '0 3 * * *' },
}

test('taskEditPatch: unchanged everything → empty patch', () => {
  assert.deepEqual(taskEditPatch(base, { ...base }), {})
})

test('taskEditPatch: only changed fields appear', () => {
  const patch = taskEditPatch(base, { ...base, prompt: 'do it better' })
  assert.deepEqual(patch, { prompt: 'do it better' })
})

test('taskEditPatch: blank name is omitted (leaves existing name alone)', () => {
  const patch = taskEditPatch(base, { ...base, name: '   ', prompt: 'x' })
  assert.equal('name' in patch, false)
  assert.deepEqual(patch, { prompt: 'x' })
})

test('taskEditPatch: a changed non-blank name is carried, trimmed', () => {
  assert.deepEqual(taskEditPatch(base, { ...base, name: '  Renamed  ' }), { name: 'Renamed' })
})

test('taskEditPatch: prompt/description trimmed before comparison', () => {
  // trims to the same value → no change
  assert.deepEqual(taskEditPatch(base, { ...base, prompt: '  do the thing  ' }), {})
})

test('taskEditPatch: model null normalises to "" (profile default)', () => {
  assert.deepEqual(taskEditPatch({ ...base, model: 'gpt-x' }, { ...base, model: null }), { model: '' })
  // already-default → no change
  assert.deepEqual(taskEditPatch({ ...base, model: '' }, { ...base, model: null }), {})
})

test('taskEditPatch: a schedule change is carried whole', () => {
  const schedule = { kind: 'manual', at: null, cron: null }
  assert.deepEqual(taskEditPatch(base, { ...base, schedule }), { schedule })
})

test('taskEditPatch: schedule equal by value → not carried', () => {
  assert.deepEqual(taskEditPatch(base, { ...base, schedule: { kind: 'cron', at: null, cron: '0 3 * * *' } }), {})
})

test('scheduleValue: carries a cron schedule through', () => {
  assert.deepEqual(scheduleValue({ kind: 'cron', at: null, cron: '0 9 * * 1-5' }), {
    kind: 'cron', at: null, cron: '0 9 * * 1-5',
  })
})

test('scheduleValue: carries a one-shot timestamp through', () => {
  assert.deepEqual(scheduleValue({ kind: 'once', at: '2026-08-01T09:00:00Z', cron: null }), {
    kind: 'once', at: '2026-08-01T09:00:00Z', cron: null,
  })
})

test('scheduleValue: a missing or unrenderable schedule reads as unset manual', () => {
  const manual = { kind: 'manual', at: null, cron: null }
  assert.deepEqual(scheduleValue(null), manual)
  assert.deepEqual(scheduleValue(undefined), manual)
  assert.deepEqual(scheduleValue({}), manual)
  // A kind the field has no preset for, and non-string at/cron, drop to the defaults.
  assert.deepEqual(scheduleValue({ kind: 'interval', at: 7, cron: {} }), manual)
})
