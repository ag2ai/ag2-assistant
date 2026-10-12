import assert from 'node:assert/strict'
import { test } from 'node:test'
import { foldEvent } from '../project.ts'
import type { ThreadItem } from '../schemas/events.ts'
import { groupToolRuns } from './toolRuns.ts'

test('replayed file, folder and chat calls retain their navigation targets', () => {
  const items: ThreadItem[] = []
  foldEvent(items, { type: 'ag2.events.ToolCallsEvent', data: { calls: [
    { name: 'read_file', arguments: JSON.stringify({ path: '~/Desktop/english/plan.md' }) },
    { name: 'list_folder', arguments: { path: '/home/user/english/reports' } },
    { name: 'read_chat', arguments: { chat_id: 'web-history' } },
    { name: 'list_chats', arguments: {} },
  ] } })
  const item = items.find(item => item.kind === 'tools')
  assert.equal(item?.kind, 'tools')
  if (item?.kind !== 'tools') return
  assert.deepEqual(item.runs.map(run => [run.detail, run.card]), [
    ['~/Desktop/english/plan.md', { kind: 'file', path: '~/Desktop/english/plan.md', name: 'plan.md' }],
    ['/home/user/english/reports', { kind: 'folder', path: '/home/user/english/reports' }],
    ['web-history', { kind: 'chat', chatId: 'web-history' }],
    ['list chats', undefined],
  ])
})

test('replayed execution tools retain complete source and distinguish code languages', () => {
  const command = 'python3 -c "print(\'one\'); print(\'two\')"'
  const code = 'import json\n\nprint("<img src=x onerror=alert(1)>")\n'
  const items: ThreadItem[] = []
  foldEvent(items, { type: 'ag2.events.ToolCallsEvent', data: { calls: [
    ...['run_shell_command', 'run_shell_local', 'run_shell_sandboxed'].map(name => ({ name, arguments: { command } })),
    ...['run_code', 'run_code_local', 'run_code_sandboxed'].map(name => ({ name, arguments: JSON.stringify({ code, language: 'python' }) })),
    { name: 'run_code', arguments: { code, language: 'python' } },
    { name: 'run_code', arguments: { code, language: 'bash' } },
  ] } })
  const item = items.find(item => item.kind === 'tools')
  assert.equal(item?.kind, 'tools')
  if (item?.kind !== 'tools') return
  assert.deepEqual(item.runs.slice(0, 3).map(run => run.card), Array(3).fill({ kind: 'execution', text: command }))
  assert.deepEqual(item.runs.slice(3, 6).map(run => run.card), Array(3).fill({ kind: 'execution', text: code, language: 'python' }))
  const groups = groupToolRuns(item.runs.filter(run => run.name === 'run code'))
  assert.deepEqual(groups.map(({ run, count }) => [run.card, count]), [
    [{ kind: 'execution', text: code, language: 'python' }, 2],
    [{ kind: 'execution', text: code, language: 'bash' }, 1],
  ])
})
