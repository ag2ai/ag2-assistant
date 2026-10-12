import assert from 'node:assert/strict'
import { test } from 'node:test'
import { groupToolRuns } from './toolRuns.ts'
import type { ToolRun } from '../schemas/events.ts'

test('identical files share one detail row across interleaved uses', () => {
  const runs: ToolRun[] = [
    { id: 1, name: 'write file', detail: 'reports/weekly.csv', at: 30, card: { kind: 'file', path: 'reports/weekly.csv', name: 'weekly.csv' } },
    { id: 2, name: 'write file', detail: 'archive/weekly.csv', at: 40 },
    { id: 3, name: 'write file', detail: 'reports/weekly.csv', at: 20 },
    { id: 4, name: 'write file', detail: 'reports/weekly.csv', at: 50 },
  ]
  const groups = groupToolRuns(runs)
  assert.deepEqual(groups.map(group => [group.run.detail, group.count, group.firstAt, group.lastAt]), [
    ['reports/weekly.csv', 3, 20, 50], ['archive/weekly.csv', 1, 40, 40],
  ])
  assert.deepEqual(groups[0].run.card, { kind: 'file', path: 'reports/weekly.csv', name: 'weekly.csv' })
  assert.equal(runs.length, 4)
})

test('distinct skill scripts and paths retain separate detail rows', () => {
  const details = ['screens · preview · one.yaml', 'screens · read_screen · one.yaml', 'screens · preview · two.yaml', 'screens · preview · one.yaml']
  const groups = groupToolRuns(details.map((detail, id) => ({ id, name: 'run skill script', detail })))
  assert.deepEqual(groups.map(group => [group.run.detail, group.count]), [
    [details[0], 2], [details[1], 1], [details[2], 1],
  ])
})

test('missing timestamps do not lose counted uses or the available time range', () => {
  const groups = groupToolRuns([undefined, 12, undefined, 15].map((at, id) => ({ id, name: 'read file', detail: 'data.json', at })))
  assert.equal(groups[0].count, 4)
  assert.equal(groups[0].firstAt, 12)
  assert.equal(groups[0].lastAt, 15)
})
