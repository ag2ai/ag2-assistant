import assert from 'node:assert/strict'
import { test } from 'node:test'
import { resolveToolPath } from './toolPaths.ts'
import type { FilesListing } from '../schemas/index.ts'

const listing: FilesListing = {
  root: '/home/user/workspace',
  dirs: ['reports', 'tasks/task-1'],
  files: ['reports/weekly.csv', 'tasks/task-1/result.md'].map(path => ({ path, name: path.split('/').pop()!, dir: path.split('/').slice(0, -1).join('/'), size: 10, modified: '2026-10-11T12:00:00Z' })),
}
const source = {
  files: async () => listing,
  listDirs: async () => ({ ok: true as const, path: '/home/user', parent: '/home', dirs: [] }),
}

test('tool filenames resolve workspace, task-relative and absolute workspace paths', async () => {
  for (const [path, expected] of [
    ['reports/weekly.csv', 'reports/weekly.csv'],
    ['./reports/weekly.csv', 'reports/weekly.csv'],
    ['result.md', 'tasks/task-1/result.md'],
    ['/home/user/workspace/reports/weekly.csv', 'reports/weekly.csv'],
    ['~/workspace/reports/weekly.csv', 'reports/weekly.csv'],
  ]) assert.equal(await resolveToolPath(path, source), expected)
  assert.equal(await resolveToolPath('missing.csv', source), null)
})

test('granted host paths keep their absolute address and expand the configured home', async () => {
  assert.equal(await resolveToolPath('~/Desktop/english/plan.md', source), '/home/user/Desktop/english/plan.md')
  assert.equal(await resolveToolPath('/home/user/workspace-other/plan.md', source), '/home/user/workspace-other/plan.md')
  assert.equal(await resolveToolPath('/home/user/Desktop/english/plan.md', source), '/home/user/Desktop/english/plan.md')
})

test('directories can be revealed without containing any files', async () => {
  assert.equal(await resolveToolPath('empty/', source, 'directory'), 'empty')
  assert.equal(await resolveToolPath('~/Desktop/english/reports/', source, 'directory'), '/home/user/Desktop/english/reports')
  assert.equal(await resolveToolPath('/home/user/workspace/empty', source, 'directory'), 'empty')
  assert.equal(await resolveToolPath('/home/user/workspace', source, 'directory'), '')
  assert.equal(await resolveToolPath('.', source, 'directory'), '')
})

test('an unavailable home and unsupported named-home paths cannot produce a link', async () => {
  const unavailable = { ...source, listDirs: async () => ({ ok: false as const, error: 'Unavailable' }) }
  assert.equal(await resolveToolPath('~/plan.md', unavailable), null)
  assert.equal(await resolveToolPath('~someone/plan.md', source), null)
})
