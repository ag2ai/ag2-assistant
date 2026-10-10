import assert from 'node:assert/strict'
import { test } from 'node:test'
import { projectScreenEvent } from './screens.ts'
import { a2uiText, childSlots } from './a2ui.ts'
import { Screen } from '../schemas/screen.ts'
import { parse, resolve } from './route.ts'

test('a Screen opens independently of Chats and routes back to a fresh Chat', () => {
  const current = { pathname: '/app/work/chats/c/old', hash: '#settings=cards' }
  const url = resolve(current, { type: 'go', path: '/screens/s/' + encodeURIComponent('dash/morning.screen.yaml') })
  const [path, hash] = url.split('#')
  assert.deepEqual(parse(path, '#' + hash), {
    name: 'screen', tab: 'screens', kind: null, id: 'dash/morning.screen.yaml', pid: 'work',
    overlay: 'settings', overlayValue: 'cards', aside: null,
  })
  assert.equal(resolve({ pathname: path }, { type: 'go', path: '/c/new' }), '/app/work/chats/c/new')
  assert.equal(parse('/app/work/screens', '').name, 'screens')
})

test('typed file events update all references while keeping independent copies unchanged', () => {
  const screen = Screen.parse({ path: 'morning.screen.yaml', title: 'Morning',
    message: { surface_id: 'screen', version: 'v1.0', catalog_id: 'catalog', title: 'Morning', intent: '',
      component: { id: 'root', component: 'Column' },
      data: { _cards: { one: { summary: 'Old' }, two: { summary: 'Old' }, copy: { summary: 'Copied' } } },
    },
    sources: Object.fromEntries(['one', 'two', 'copy'].map(slot => [slot, {
      slot, path: slot === 'copy' ? 'copy.card-instance.yaml' : 'reading.card-instance.yaml',
      surface_id: 'same-copied-identity', source_id: 'root', source: { tool: 'get_weather', fields: {} },
    }])),
  })
  const data = { summary: 'Fresh', _sources: { root: { tool: 'get_weather', fields: {} } } }
  const event = { type: 'assistant.events.CardSourceUpdated', data: {
    surface_id: 'same-copied-identity', source_id: 'root', path: 'reading.card-instance.yaml',
    status: 'updated', error: '', data, code_version: '',
  } }
  assert.deepEqual(Object.keys(projectScreenEvent(screen, event)), ['one', 'two'])
  assert.deepEqual(screen.message.data._cards, { one: data, two: data, copy: { summary: 'Copied' } })
  projectScreenEvent(screen, { ...event, data: { ...event.data, status: 'error', data: {} } })
  assert.deepEqual(screen.message.data._cards, { one: data, two: data, copy: { summary: 'Copied' } })
  assert.deepEqual(projectScreenEvent(screen, { ...event, data: { ...event.data, surface_id: 'other-profile' } }), {})
})

test('namespaced absolute paths and repeated relative paths resolve in the browser', () => {
  const data = { _cards: { 'a/b~c': { title: 'Heading', rows: [{ title: 'First' }, { title: 'Second' }] } } }
  assert.equal(a2uiText({ text: { path: '/_cards/a~1b~0c/title' } }, data), 'Heading')
  const slots = childSlots({ componentId: 'row', path: '/_cards/a~1b~0c/rows' }, data)
  assert.deepEqual(slots.map(slot => a2uiText({ text: { path: './title' } }, data, slot.scope)), ['First', 'Second'])
})
