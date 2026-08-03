import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Folder, FolderMutated, FolderRoots, FolderSaved } from './folder.ts'

test('Folder carries its grants with the resolved mode', () => {
  const parsed = Folder.parse({
    id: 'f1', name: 'repo', path: '/home/u/repo', exists: true,
    grants: [{ profile: 'p1', chat_id: '', task_id: '', mode: 'read_write' }],
  })
  assert.equal(parsed.grants[0].mode, 'read_write')
})

test('Folder rejects a grant mode outside read/read_write/none', () => {
  assert.throws(() =>
    Folder.parse({
      id: 'f1', name: 'repo', path: '/p', exists: false,
      grants: [{ profile: 'p1', chat_id: '', task_id: '', mode: 'write' }],
    }),
  )
})

test('Folder keeps a chat-scoped none grant — the per-chat block, not an absence', () => {
  const parsed = Folder.parse({
    id: 'f1', name: 'repo', path: '/p', exists: true,
    grants: [
      { profile: 'p1', chat_id: '', task_id: '', mode: 'read' },
      { profile: 'p1', chat_id: 'web-abc', task_id: '', mode: 'none' },
    ],
  })
  assert.equal(parsed.grants[1].mode, 'none')
})

test('FolderRoots keeps a missing-path root instead of dropping it', () => {
  const parsed = FolderRoots.parse({
    roots: [{ id: 'f1', name: 'gone', path: '/nope', mode: 'read', exists: false }],
  })
  assert.equal(parsed.roots[0].exists, false)
})

test('FolderSaved echoes the changed folder alongside the whole snapshot', () => {
  const folder = { id: 'f1', name: 'repo', path: '/p', exists: true, grants: [] }
  const parsed = FolderSaved.parse({ ok: true, folder, folders: [folder] })
  assert.equal(parsed.folder.id, parsed.folders[0].id)
})

test('FolderMutated carries only the snapshot, as delete and grant routes return', () => {
  const parsed = FolderMutated.parse({ ok: true, folders: [] })
  assert.equal(parsed.folders.length, 0)
})
