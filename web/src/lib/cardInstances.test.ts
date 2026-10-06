import { test } from 'node:test'
import assert from 'node:assert/strict'
import { captureInstance, instanceFilename } from './cardInstances.ts'
import { viewerKind } from './preview.ts'
import { foldEvent } from '../project.ts'
import type { ThreadItem } from '../schemas/events.ts'

const item: Extract<ThreadItem, { kind: 'a2ui' }> = {
  id: 1, kind: 'a2ui', surfaceId: 'source', title: 'Reading list',
  component: { id: 'root', component: 'Text', text: { path: '/note' } },
  data: { note: 'Server value' }, draft: { id: 'draft', version: 1, name: 'Shelf' },
}

test('Save proposes an available filename in the selected Directory', () => {
  assert.equal(instanceFilename('Reading list', 'notes', ['notes/reading-list.card-instance.yaml', 'notes/reading-list-2.card-instance.yaml']), 'reading-list-3.card-instance.yaml')
  assert.equal(instanceFilename('Reading list', '', ['notes/reading-list.card-instance.yaml']), 'reading-list.card-instance.yaml')
})

test('Save captures the complete local model and expanded tree independently of later edits', () => {
  const local = { note: 'Visible edit', rows: [{ text: 'Retain me' }] }
  const copy = captureInstance(item, local, 'notes/copy.card-instance.yaml', 'request')
  local.rows[0].text = 'Later edit'
  item.component.text = 'Later layout'
  assert.deepEqual(copy.message.data, { note: 'Visible edit', rows: [{ text: 'Retain me' }] })
  assert.deepEqual(copy.message.component, { id: 'root', component: 'Text', text: { path: '/note' }, _components: [{ id: 'root', component: 'Text', text: { path: '/note' } }] })
  assert.equal('draft' in copy.message, false)
  assert.equal(copy.request_id, 'request')
})

test('Only the dedicated instance suffix selects the Card preview', () => {
  assert.equal(viewerKind('answer.card-instance.yaml'), 'card-instance')
  assert.equal(viewerKind('answer.card.yaml'), 'code')
  assert.equal(viewerKind('answer.yaml'), 'code')
})

test('A repeated Save notice projects one Open action without modifying its source', () => {
  const items: ThreadItem[] = [{ ...item, data: { note: 'Original' } }]
  const event = { type: 'assistant.events.CardInstanceSaved', data: {
    surface_id: 'source', path: 'notes/copy.card-instance.yaml', instance_id: 'copy',
    request_id: 'request', saved_at: '2026-10-06T10:00:00Z',
  } }
  foldEvent(items, event)
  foldEvent(items, event)
  assert.equal(items.length, 2)
  assert.equal(items[1].kind === 'note' && items[1].savedPath, 'notes/copy.card-instance.yaml')
  assert.deepEqual(items[0].kind === 'a2ui' && items[0].data, { note: 'Original' })
})
