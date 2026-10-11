import assert from 'node:assert/strict'
import { test } from 'node:test'
import { foldEvent } from '../project.ts'
import type { ThreadItem } from '../schemas/events.ts'

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
