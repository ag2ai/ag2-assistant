import assert from 'node:assert/strict'
import { test } from 'node:test'
import { get } from 'svelte/store'
import { JSDOM } from 'jsdom'
import { setUnsavedGuard } from './lib/unsavedGuard.ts'

const dom = new JSDOM('', { url: 'http://localhost/app/work/chats/c/selected' })
for (const name of ['window', 'location', 'history', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true })
}
const { go, goTab, switchTab, openAsideFile, closeAside, openOverlay, route } = await import('./router.ts')

function navigate(path: string): void {
  history.replaceState({}, '', path)
  window.dispatchEvent(new dom.window.PopStateEvent('popstate'))
}

test('content Tabs restore their selected Pages while Files keeps the current Page', () => {
  navigate('/app/work/chats/c/selected')
  switchTab('tasks')
  assert.equal(location.pathname, '/app/work/tasks')
  go('/t/task-1')
  switchTab('screens')
  go('/screens/s/dashboard.screen.yaml')
  switchTab('files')
  openAsideFile('notes/selected.md')
  const expected = {
    chats: '/app/work/chats/c/selected',
    tasks: '/app/work/tasks/t/task-1',
    screens: '/app/work/screens/s/dashboard.screen.yaml',
    files: '/app/work/files',
  } as const
  for (const tab of ['chats', 'screens', 'tasks', 'files', 'chats', 'tasks', 'screens', 'files'] as const) {
    const before = get(route)
    switchTab(tab)
    const filePage = before.name === 'screen' ? '/s/' + before.id : '/' + before.kind + '/' + before.id
    assert.equal(location.pathname, tab === 'files' ? '/app/work/files' + filePage : expected[tab])
    assert.deepEqual(get(route).aside, { kind: 'file', path: 'notes/selected.md' })
  }
})

test('selecting another item updates only that Tab and clicking the active Tab keeps it open', () => {
  navigate('/app/reselect/chats/c/first')
  switchTab('tasks')
  go('/t/first-task')
  switchTab('chats')
  go('/c/second')
  switchTab('screens')
  go('/screens/s/dashboard')
  switchTab('screens')
  assert.equal(get(route).id, 'dashboard')
  switchTab('chats')
  assert.equal(get(route).id, 'second')
  switchTab('tasks')
  assert.equal(get(route).id, 'first-task')
})

test('reloaded Tabs restore their saved Pages and keep the current Settings modal', () => {
  for (const [tab, page] of Object.entries({
    chats: '/app/reloaded/chats/c/chat%20%2F%201',
    tasks: '/app/reloaded/tasks/r/run-1',
    screens: '/app/reloaded/screens/s/dashboard%20%26%20one',
    files: '/app/reloaded/files#aside=file:notes/a%20b.md',
  })) sessionStorage.setItem('ag2-selected-page:reloaded:' + tab, page)
  navigate('/app/reloaded/screens/s/current')
  openOverlay('settings', 'models')
  switchTab('chats')
  assert.equal(get(route).id, 'chat / 1')
  switchTab('tasks')
  assert.equal(get(route).id, 'run-1')
  assert.equal(get(route).kind, 'r')
  switchTab('files')
  assert.equal(get(route).aside, null)
  assert.equal(get(route).id, 'run-1')
  assert.equal(get(route).overlay, 'settings')
  assert.equal(get(route).overlayValue, 'models')
})

test('each profile keeps separate selections', () => {
  navigate('/app/source/chats/c/private-chat')
  switchTab('tasks')
  go('/t/private-task')
  switchTab('screens', 'destination')
  go('/screens/s/public-dashboard')
  switchTab('chats')
  assert.equal(location.pathname, '/app/destination/chats')
  go('/c/destination-chat')
  switchTab('chats', 'source')
  assert.equal(location.pathname, '/app/source/chats/c/private-chat')
  switchTab('tasks')
  assert.equal(get(route).id, 'private-task')
  switchTab('screens', 'destination')
  assert.equal(get(route).id, 'public-dashboard')
})

test('explicitly closing a task remembers the empty Tasks page', () => {
  navigate('/app/deleting/tasks/t/deleted')
  goTab('tasks')
  switchTab('screens')
  switchTab('tasks')
  assert.equal(location.pathname, '/app/deleting/tasks')
  assert.equal(get(route).id, null)
})

test('browser history updates the remembered Page', () => {
  navigate('/app/history/chats/c/later')
  navigate('/app/history/chats/c/earlier')
  switchTab('screens')
  switchTab('chats')
  assert.equal(get(route).id, 'earlier')
})

test('Files keeps the current thread scope on its first visit and explicit Reveal navigation', () => {
  navigate('/app/files-scope/chats/c/with-folder-grants')
  switchTab('files')
  assert.equal(location.pathname, '/app/files-scope/files/c/with-folder-grants')
  switchTab('tasks')
  go('/t/other-folder-grants')
  go('/files')
  assert.equal(location.pathname, '/app/files-scope/files/t/other-folder-grants')
})

test('closing a file preview is remembered when returning to Files', () => {
  navigate('/app/file-close/files#aside=file:notes.md')
  closeAside()
  switchTab('screens')
  switchTab('files')
  assert.equal(get(route).aside, null)
})

test('switching Drawer Tabs keeps a dirty editor without a discard prompt', () => {
  navigate('/app/dirty/chats/c/chat-1')
  switchTab('files')
  openAsideFile('notes.md')
  Object.defineProperty(globalThis, 'confirm', { value: () => { throw new Error('Unexpected discard prompt') }, configurable: true })
  setUnsavedGuard(() => true)
  try {
    switchTab('chats')
    assert.equal(get(route).tab, 'chats')
    assert.equal(get(route).id, 'chat-1')
    assert.deepEqual(get(route).aside, { kind: 'file', path: 'notes.md' })
  } finally {
    setUnsavedGuard(null)
    Reflect.deleteProperty(globalThis, 'confirm')
  }
})

for (const [kind, name] of [['c', 'chat'], ['t', 'task'], ['r', 'run']] as const) {
  test(`returning to Files keeps the current ${name} instead of its remembered Page`, () => {
    const pid = 'files-current-' + kind
    sessionStorage.setItem('ag2-selected-page:' + pid + ':files', '/app/' + pid + '/files#aside=file:stale.md')
    navigate('/app/' + pid + '/chats/' + kind + '/current#aside=file:current.md')
    switchTab('files')
    assert.equal(location.pathname, '/app/' + pid + '/files/' + kind + '/current')
    assert.equal(get(route).name, name)
    assert.deepEqual(get(route).aside, { kind: 'file', path: 'current.md' })
    switchTab('tasks')
    go('/' + kind + '/next')
    switchTab('files')
    assert.equal(get(route).id, 'next')
    assert.equal(get(route).name, name)
    window.dispatchEvent(new dom.window.PopStateEvent('popstate'))
    assert.equal(get(route).id, 'next')
  })
}

test('Files keeps the current Screen and file preview', () => {
  navigate('/app/files-screen/screens/s/dashboard%20%26%20one#aside=file:notes.md')
  switchTab('files')
  assert.equal(location.pathname, '/app/files-screen/files/s/dashboard%20%26%20one')
  assert.equal(get(route).name, 'screen')
  assert.equal(get(route).id, 'dashboard & one')
  assert.deepEqual(get(route).aside, { kind: 'file', path: 'notes.md' })
})

test('Files keeps a dirty editor without prompting to discard it', () => {
  sessionStorage.setItem('ag2-selected-page:files-dirty:files', '/app/files-dirty/files#aside=file:stale.md')
  navigate('/app/files-dirty/chats/c/current#aside=file:editing.md')
  Object.defineProperty(globalThis, 'confirm', { value: () => { throw new Error('Unexpected discard prompt') }, configurable: true })
  setUnsavedGuard(() => true)
  try {
    switchTab('files')
    assert.equal(get(route).tab, 'files')
    assert.equal(get(route).id, 'current')
    assert.deepEqual(get(route).aside, { kind: 'file', path: 'editing.md' })
  } finally {
    setUnsavedGuard(null)
    Reflect.deleteProperty(globalThis, 'confirm')
  }
})

test('the selected file stays in the right rail across all Drawer Tabs', () => {
  navigate('/app/independent-preview/chats/c/chat-1')
  switchTab('tasks')
  go('/t/task-1')
  switchTab('screens')
  go('/screens/s/dashboard')
  switchTab('chats')
  switchTab('files')
  openAsideFile('selected.md')
  for (const tab of ['chats', 'tasks', 'screens', 'files', 'chats'] as const) {
    switchTab(tab)
    assert.deepEqual(get(route).aside, { kind: 'file', path: 'selected.md' })
  }
  closeAside()
  for (const tab of ['tasks', 'screens', 'files', 'chats'] as const) {
    switchTab(tab)
    assert.equal(get(route).aside, null)
  }
})
