// Date breakpoints: verify that realistic chat history projects to the right
// dividers. Folds mock wire events (with `created_at`) through the REAL foldEvent
// projection, then runs the REAL dayRows the thread view renders — so this tests
// the actual grouping, not a re-implementation of it.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { foldEvent } from '../project.ts'
import type { ThreadItem, WireEvent } from '../schemas/events.ts'
import { dayKey, fmtDay, fmtDayShort, dayRows, taskRecencyAt } from './time.ts'

// Local noon of (today + offset), as Unix seconds — matches AG2 `created_at`.
// Anchored at noon so a message never sits near midnight and flips its day.
const dayAt = (offset: number) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  d.setHours(12, 0, 0, 0)
  return d.getTime() / 1000
}
// The label fmtDay is expected to produce for a given Unix-seconds moment:
// "Today at 5:24 PM" / "Yesterday at …" / "Fri, Jun 26 at …" (localized), always
// with the time, year only when not the current one.
const clock = (sec: number) => new Date(sec * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
const label = (sec: number) => {
  const d = new Date(sec * 1000)
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate())
  const diff = Math.round((startOfDay(d).getTime() - startOfDay(now).getTime()) / 86400000)
  let day
  if (diff === 0) day = 'Today'
  else if (diff === -1) day = 'Yesterday'
  else {
    const opts: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' }
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
    day = d.toLocaleDateString([], opts)
  }
  return `${day} at ${clock(sec)}`
}
const user = (text: string, created_at: number): WireEvent => ({
  type: 'ag2.events.ModelRequest',
  data: { parts: [{ __event__: 'ag2.events.TextInput', content: text }], created_at },
})
const agent = (text: string, created_at: number): WireEvent => ({
  type: 'ag2.events.ModelResponse',
  data: { message: { content: text }, created_at },
})

test('items are stamped with their event created_at', () => {
  const items: ThreadItem[] = []
  const at = dayAt(-1)
  foldEvent(items, user('hi', at))
  assert.equal(items[0].at, at)
})

test('a divider precedes the first item of each day, stamped with that day\'s first message time', () => {
  const items: ThreadItem[] = []
  const d3 = dayAt(-3), d1 = dayAt(-1), d0 = dayAt(0)
  foldEvent(items, user('three days ago', d3))
  foldEvent(items, agent('reply', d3 + 60))
  foldEvent(items, user('yesterday', d1))
  foldEvent(items, agent('reply', d1 + 60))
  foldEvent(items, user('today', d0))
  foldEvent(items, agent('reply', d0 + 3600)) // an hour later, same day

  const seps = dayRows(items).map((r) => r.sep)
  // One divider per day, on the first item; same-day siblings get null.
  assert.deepEqual(seps, [label(d3), null, label(d1), null, label(d0), null])
  assert.equal(seps.filter(Boolean).length, 3)
  // Today/Yesterday resolve; the label carries the FIRST message's time (noon),
  // not the later reply's (an hour on).
  assert.equal(seps[4], `Today at ${clock(d0)}`)
  assert.equal(seps[2], `Yesterday at ${clock(d1)}`)
  assert.notEqual(seps[4], label(d0 + 3600))
})

test('an item with no timestamp draws no divider and does not reset the day', () => {
  const items: ThreadItem[] = []
  const d0 = dayAt(0)
  foldEvent(items, user('morning', d0))
  // A tool card can arrive without a created_at → a timeless item mid-day.
  foldEvent(items, { type: 'ag2.events.ToolCallsEvent', data: { calls: [{ name: 'web_search' }] } })
  foldEvent(items, agent('afternoon', d0 + 120))

  assert.equal(items.length, 3)
  assert.equal(items[1].at, undefined) // the tool card has no time
  const seps = dayRows(items).map((r) => r.sep)
  // Only the first same-day item gets a label; the timeless tool card and the
  // later same-day reply stay divider-free (the tool card didn't reset the day).
  assert.deepEqual(seps, [label(d0), null, null])
})

test('two moments on the same day share a dayKey; different days differ', () => {
  const morning = dayAt(0)
  const evening = morning + 8 * 3600
  assert.equal(dayKey(morning), dayKey(evening))
  assert.notEqual(dayKey(morning), dayKey(dayAt(-1)))
  assert.equal(dayKey(null), null)
  assert.equal(dayKey(''), null)
})

test('fmtDay: Today/Yesterday/absolute date, always with the first-message time', () => {
  const today = dayAt(0)
  assert.equal(fmtDay(today), `Today at ${clock(today)}`)
  const yesterday = dayAt(-1)
  assert.equal(fmtDay(yesterday), `Yesterday at ${clock(yesterday)}`)
  // Older than yesterday → an absolute date (no Today/Yesterday), still timed.
  const older = dayAt(-4)
  assert.match(fmtDay(older), / at /)
  assert.doesNotMatch(fmtDay(older), /Today|Yesterday/)
  // This year → no year in the label; a prior year → year included.
  assert.doesNotMatch(fmtDay(today), new RegExp(String(new Date().getFullYear())))
  const priorYear = new Date(2000, 0, 15, 12).getTime() / 1000
  assert.match(fmtDay(priorYear), /2000/)
})

// --- Chats-list date section headers (fmtDayShort + dayRows) -------------------
// A chat row's ISO `updated` (last-message time) at local noon of (today+offset),
// noon-anchored so it can't drift across midnight.
const isoAt = (offset: number) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  d.setHours(12, 0, 0, 0)
  return d.toISOString()
}
// A drawer chat row: dayRows keys off `at`, which the drawer maps from `updated`.
const chat = (id: string, offset: number) => ({ chat_id: id, at: isoAt(offset) })

test('fmtDayShort: today is "Recent", then "Yesterday", then a date — never a time', () => {
  assert.equal(fmtDayShort(isoAt(0)), 'Recent')
  assert.equal(fmtDayShort(isoAt(-1)), 'Yesterday')
  const older = fmtDayShort(isoAt(-4))
  assert.doesNotMatch(older, /Recent|Yesterday|Today/)
  assert.doesNotMatch(older, / at |:\d\d/) // date-only: no "at TIME", no clock
  // This year → no year; a prior year → year included.
  assert.doesNotMatch(fmtDayShort(isoAt(0)), new RegExp(String(new Date().getFullYear())))
  assert.match(fmtDayShort(new Date(2000, 0, 15, 12).toISOString()), /2000/)
})

test('chats list: one header per day, on the first row of each day (newest-first)', () => {
  const rows = dayRows(
    [chat('a', 0), chat('b', 0), chat('c', -1), chat('d', -4)],
    fmtDayShort,
  )
  const seps = rows.map((r) => r.sep)
  // "Recent" leads and spans both same-day chats; "Yesterday"; then a bare date.
  assert.equal(seps[0], 'Recent')
  assert.equal(seps[1], null) // second same-day chat: no repeat header
  assert.equal(seps[2], 'Yesterday')
  assert.match(String(seps[3]), /\w+ \d/) // "Thu, Jul 11"-style
  assert.equal(seps.filter(Boolean).length, 3)
})

test('chats list: a chat with a blank `updated` gets no header and rides under the last one', () => {
  const rows = dayRows([chat('a', 0), { chat_id: 'b', at: '' }], fmtDayShort)
  const seps = rows.map((r) => r.sep)
  assert.deepEqual(seps, ['Recent', null]) // blank-updated chat: no header
})

// --- Tasks-list recency key (taskRecencyAt) -----------------------------------
// The task analogue of a chat's `updated`: the last run's end time, or its start
// when still running/waiting, falling back to creation for a task that never ran.
test('taskRecencyAt: a finished run keys off its ended_at', () => {
  const t = { created_at: isoAt(-9), last_run: { ended_at: isoAt(-1), started_at: isoAt(-1) } }
  assert.equal(taskRecencyAt(t), t.last_run.ended_at)
})
test('taskRecencyAt: a run with no end (running/waiting) keys off started_at', () => {
  const t = { created_at: isoAt(-9), last_run: { ended_at: null, started_at: isoAt(0) } }
  assert.equal(taskRecencyAt(t), t.last_run.started_at)
})
test('taskRecencyAt: a never-run task falls back to created_at', () => {
  const t = { created_at: isoAt(-3), last_run: null }
  assert.equal(taskRecencyAt(t), t.created_at)
})
test('taskRecencyAt: a run with neither timestamp falls back to created_at', () => {
  const t = { created_at: isoAt(-3), last_run: { ended_at: null, started_at: null } }
  assert.equal(taskRecencyAt(t), t.created_at)
})
