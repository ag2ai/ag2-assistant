import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isFolderPath, rawQuery, modeLabel, folderAncestorDirs, folderAffordances } from './folderFiles.ts'

test('folderAffordances: read_write unlocks every mutation; read/none/unknown unlock none', () => {
  assert.deepEqual(folderAffordances('read_write'), { edit: true, rename: true, delete: true, move: true })
  assert.deepEqual(folderAffordances('read'), { edit: false, rename: false, delete: false, move: false })
  assert.deepEqual(folderAffordances('none'), { edit: false, rename: false, delete: false, move: false })
  assert.deepEqual(folderAffordances(''), { edit: false, rename: false, delete: false, move: false })
  assert.deepEqual(folderAffordances(undefined), { edit: false, rename: false, delete: false, move: false })
})

test('modeLabel: read_write reads as read+write; read as read; anything else is blank', () => {
  assert.equal(modeLabel('read_write'), 'read+write')
  assert.equal(modeLabel('read'), 'read')
  assert.equal(modeLabel('none'), '')
  assert.equal(modeLabel(''), '')
  assert.equal(modeLabel(undefined), '')
})

test('isFolderPath: absolute and ~-relative are Folder files; relative is Files-space', () => {
  assert.equal(isFolderPath('/data/acme/widget.py'), true)
  assert.equal(isFolderPath('~/repo/x.md'), true)
  assert.equal(isFolderPath('docs/report.txt'), false)
  assert.equal(isFolderPath('report.txt'), false)
  assert.equal(isFolderPath(''), false)
  assert.equal(isFolderPath(null), false)
})

test('rawQuery: an absolute (Folder) path carries chat_id', () => {
  assert.equal(
    rawQuery('/data/acme/widget.py', { chatId: 'web-abc' }),
    'path=' + encodeURIComponent('/data/acme/widget.py') + '&chat_id=web-abc'
  )
})

test('rawQuery: a relative (Files-space) path never carries chat_id', () => {
  assert.equal(
    rawQuery('docs/report.txt', { chatId: 'web-abc' }),
    'path=' + encodeURIComponent('docs/report.txt')
  )
})

test('rawQuery: download flips to attachment; chat_id rides alongside for a Folder path', () => {
  assert.equal(
    rawQuery('/data/acme/a.png', { download: true, chatId: 'c1' }),
    'path=' + encodeURIComponent('/data/acme/a.png') + '&download=true&chat_id=c1'
  )
  // an empty chatId is dropped even for a Folder path
  assert.equal(
    rawQuery('/data/acme/a.png', { download: true }),
    'path=' + encodeURIComponent('/data/acme/a.png') + '&download=true'
  )
})

test('folderAncestorDirs: root down to the file parent, shallowest first, absolute', () => {
  assert.deepEqual(
    folderAncestorDirs('/data/acme', '/data/acme/src/lib/x.md'),
    ['/data/acme', '/data/acme/src', '/data/acme/src/lib']
  )
})

test('folderAncestorDirs: a file directly in the root yields just the root', () => {
  assert.deepEqual(folderAncestorDirs('/data/acme', '/data/acme/notes.md'), ['/data/acme'])
})

test('folderAncestorDirs: a trailing slash on the root is tolerated', () => {
  assert.deepEqual(folderAncestorDirs('/data/acme/', '/data/acme/c/x.md'), ['/data/acme', '/data/acme/c'])
})

test('folderAncestorDirs: a path outside the root is []', () => {
  assert.deepEqual(folderAncestorDirs('/data/acme', '/data/other/x.md'), [])
  assert.deepEqual(folderAncestorDirs('/data/acme', '/data/acme'), [])   // the root itself is not a file under it
  assert.deepEqual(folderAncestorDirs('/data/acmex', '/data/acme/x.md'), [])   // sibling prefix, not a parent
  assert.deepEqual(folderAncestorDirs('', '/data/acme/x.md'), [])
})

test('rawQuery: two same-named files in different roots build distinct calls', () => {
  const a = rawQuery('/data/acme/notes.md', { chatId: 'c1' })
  const b = rawQuery('/data/other/notes.md', { chatId: 'c1' })
  assert.notEqual(a, b)
  assert.match(a, /acme/)
  assert.match(b, /other/)
})
