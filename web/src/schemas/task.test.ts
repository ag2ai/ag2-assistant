import { test } from 'node:test'
import assert from 'node:assert/strict'
import { HitlPending, Inquiry, Run, RunDetail, RunDetailEnvelope, Task, TaskWithRuns } from './task.ts'

const run = {
  id: 'run-1', task_id: 't1', status: 'completed', trigger: 'manual',
  started_at: '2026-08-01T10:00:00+03:00', ended_at: '2026-08-01T10:01:00+03:00',
  summary: 'done', error: '', seen: true,
}

const task = {
  id: 't1', name: 'Daily digest', prompt: 'summarise', model: null, description: '',
  schedule: { kind: 'cron', at: null, cron: '0 9 * * *' }, schedule_desc: 'every day at 9',
  paused: false, starred: false, recall_depth: 0, next_run_at: null,
  created_at: '2026-08-01T09:00:00+03:00', updated_at: '2026-08-01T09:00:00+03:00',
  last_run: run, unread: 0, needs_input: false,
}

test('Run accepts every lifecycle status', () => {
  for (const status of ['running', 'needs_input', 'completed', 'failed', 'cancelled']) {
    assert.equal(Run.parse({ ...run, status }).status, status)
  }
})

test('Run rejects an unknown status', () => {
  assert.throws(() => Run.parse({ ...run, status: 'paused' }))
})

test('Run accepts a run still in flight, with no end stamp or summary', () => {
  const parsed = Run.parse({ ...run, status: 'running', ended_at: null, summary: null })
  assert.equal(parsed.ended_at, null)
})

test('Task accepts a null model, null next_run_at and a null last_run', () => {
  const parsed = Task.parse({ ...task, model: null, next_run_at: null, last_run: null })
  assert.equal(parsed.last_run, null)
})

test('Task keeps the schedule fields beyond kind', () => {
  const parsed = Task.parse(task)
  assert.equal(parsed.schedule.kind, 'cron')
  assert.equal((parsed.schedule as Record<string, unknown>).cron, '0 9 * * *')
})

test('Task requires a schedule — the service always normalises one', () => {
  // model.py:87 defaults it and normalize_schedule(None) yields manual, so a null
  // here would be a backend contract change, not a case the UI must render.
  assert.throws(() => Task.parse({ ...task, schedule: null }))
})

test('Task rejects a schedule kind the backend does not define', () => {
  assert.throws(() => Task.parse({ ...task, schedule: { kind: 'hourly' } }))
})

test('TaskWithRuns carries the run history', () => {
  assert.equal(TaskWithRuns.parse({ ...task, runs: [run] }).runs.length, 1)
})

test('Inquiry links a run back to its root task', () => {
  const parsed = Inquiry.parse({
    id: 'i1', task_id: 'run-1', chat: 'task-run:run-1', kind: 'question',
    text: 'proceed?', detail: '', options: ['yes', 'no'],
    created_at: '2026-08-01T10:00:30+03:00',
    root_id: 't1', task_title: 'Daily digest', run_id: 'run-1',
  })
  assert.equal(parsed.root_id, 't1')
})

test('Inquiry raised outside a run carries null links', () => {
  const parsed = Inquiry.parse({
    id: 'i2', task_id: '', chat: 'c1', kind: 'permission',
    text: 'allow?', detail: '', options: [], created_at: '2026-08-01T10:00:30+03:00',
    root_id: null, task_title: '', run_id: null,
  })
  assert.equal(parsed.run_id, null)
})

test('HitlPending parses this profile own question registry', () => {
  const parsed = HitlPending.parse({
    pending: [{ id: 'q1', text: 'ok?', detail: '', options: [], kind: 'question', path: '/tmp/q1' }],
  })
  assert.equal(parsed.pending[0].id, 'q1')
})

test('RunDetail keeps the task_name the run header renders', () => {
  const parsed = RunDetail.parse({ ...run, task_name: 'Daily digest' })
  assert.equal(parsed.task_name, 'Daily digest')
})

test('RunDetailEnvelope wraps the run the run routes return', () => {
  const parsed = RunDetailEnvelope.parse({ run: { ...run, task_name: '' } })
  assert.equal(parsed.run.task_name, '')
})
