import { test } from 'node:test'
import assert from 'node:assert/strict'
import { FilesResponse, Mentions, SearchResults, WriteResult } from './file.ts'

test('FilesResponse parses the Files-space branch', () => {
  const parsed = FilesResponse.parse({
    root: '/home/u/ws',
    files: [{ path: 'a.md', name: 'a.md', dir: '', size: 12, modified: '2026-08-01T10:00:00+03:00' }],
    dirs: ['notes'],
  })
  assert.equal('root' in parsed, true)
})

test('FilesResponse parses the Folder branch with its resolved mode', () => {
  const parsed = FilesResponse.parse({
    path: '/home/u/repo',
    dirs: [{ name: 'src', path: '/home/u/repo/src' }],
    files: [{ name: 'go.mod', path: '/home/u/repo/go.mod', size: 40 }],
    mode: 'read',
  })
  assert.equal('mode' in parsed && parsed.mode, 'read')
})

test('SearchResults carries absolute paths and a closed kind', () => {
  const parsed = SearchResults.parse({
    results: [{ path: '/abs/a.md', name: 'a.md', dir: 'notes', kind: 'file' }],
  })
  assert.equal(parsed.results[0].path, '/abs/a.md')
  assert.throws(() =>
    SearchResults.parse({ results: [{ path: '/a', name: 'a', dir: '', kind: 'symlink' }] }),
  )
})

test('Mentions distinguishes a run row from a chat row', () => {
  const parsed = Mentions.parse({
    threads: [
      { stream_id: 'c1', kind: 'chat', title: 'hi', updated: '2026-08-01T10:00:00+03:00' },
      {
        stream_id: 'task-run:r1', kind: 'run', title: 'Digest',
        updated: '2026-08-01T11:00:00+03:00', task_id: 't1', task_name: 'Digest',
        run_started_at: '2026-08-01T10:59:00+03:00',
      },
    ],
  })
  assert.equal(parsed.threads[1].kind, 'run')
})

test('Mentions rejects a run row missing its task backlink', () => {
  assert.throws(() =>
    Mentions.parse({
      threads: [{ stream_id: 'task-run:r1', kind: 'run', title: 'x', updated: '' }],
    }),
  )
})

test('WriteResult carries the new content token from the body', () => {
  assert.equal(WriteResult.parse({ ok: true, etag: 'abc123' }).etag, 'abc123')
})
