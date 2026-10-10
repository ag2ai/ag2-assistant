import assert from 'node:assert/strict'
import { test } from 'node:test'
import { foldEvent } from './project.ts'
import { currentDraft } from './lib/cardDrafts.ts'
import type { ThreadItem, WireEvent } from './schemas/events.ts'

const draft = (id: string, version: number): WireEvent => ({
  type: 'assistant.events.EphemeralCard',
  data: {
    draft_id: id, draft_version: version, surface_id: `${id}-${version}`,
    definition: { name: id }, component: { id: 'root', component: 'Text', text: id },
    data: { title: id },
  },
})

test('live and replay retain independent drafts and only current versions can be saved', () => {
  const items: ThreadItem[] = []
  const events = [draft('graph', 1), draft('calendar', 1), draft('graph', 2), draft('calendar', 2), draft('graph', 3)]
  for (const event of events) foldEvent(items, event)
  const surfaces = items.filter(item => item.kind === 'a2ui')
  assert.equal(surfaces.length, 5)
  assert.deepEqual(surfaces.filter(item => currentDraft(items, item)).map(item => item.surfaceId), ['calendar-2', 'graph-3'])
  const replay: ThreadItem[] = []
  for (const event of events) foldEvent(replay, event)
  assert.deepEqual(replay.filter(item => item.kind === 'a2ui').filter(item => currentDraft(replay, item)).map(item => item.surfaceId), ['calendar-2', 'graph-3'])
  const update: WireEvent = {type: 'assistant.events.A2UISurfaceDataUpdated', data: {
    surface_id: 'graph-3', data: {title: 'Edited graph'},
  }}
  foldEvent(replay, update)
  const edited = replay.find(item => item.kind === 'a2ui' && item.surfaceId === 'graph-3')
  assert.ok(edited?.kind === 'a2ui')
  assert.deepEqual(edited.data, {title: 'Edited graph'})
  assert.equal(edited.draft?.version, 3)
  assert.equal(replay.filter(item => item.kind === 'a2ui').filter(item => currentDraft(replay, item)).length, 2)
})
