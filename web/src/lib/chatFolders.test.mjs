import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatChips, inheritedCount, addPlan } from './chatFolders.js'

const PID = 'work'
const CHAT = 'web-abc'
const TASK = 'task-1'

// A folders snapshot: id/name/path/exists + grants[{profile,chat_id,mode}].
const snap = [
  { id: 'f1', name: 'media', path: '/data/media', exists: true, grants: [
    { profile: PID, chat_id: CHAT, mode: 'read' },        // chat-only chip
  ] },
  { id: 'f2', name: 'docs', path: '/data/docs', exists: true, grants: [
    { profile: PID, chat_id: '', mode: 'read_write' },   // profile-scoped
  ] },
  { id: 'f3', name: 'both', path: '/data/both', exists: true, grants: [
    { profile: PID, chat_id: '', mode: 'read' },          // profile + chat override (widened)
    { profile: PID, chat_id: CHAT, mode: 'read_write' },
  ] },
  { id: 'f4', name: 'other-chat', path: '/data/x', exists: true, grants: [
    { profile: PID, chat_id: 'web-zzz', mode: 'read' },   // a different chat
  ] },
  { id: 'f5', name: 'other-profile', path: '/data/y', exists: true, grants: [
    { profile: 'home', chat_id: '', mode: 'read' },       // a different profile
  ] },
  { id: 'f6', name: 'blocked', path: '/data/blocked', exists: true, grants: [
    { profile: PID, chat_id: '', mode: 'read' },          // profile folder,
    { profile: PID, chat_id: CHAT, mode: 'none' },        // blocked for this chat
  ] },
  { id: 'f7', name: 'taskonly', path: '/data/t', exists: true, grants: [
    { profile: PID, chat_id: '', task_id: TASK, mode: 'read' },   // task grant, no profile grant behind it
  ] },
  { id: 'f8', name: 'taskblocked', path: '/data/tb', exists: true, grants: [
    { profile: PID, chat_id: '', mode: 'read' },                  // profile grant,
    { profile: PID, chat_id: '', task_id: TASK, mode: 'none' },   // task blocked it for this task's runs
  ] },
]

// Isolated fixtures for the chat + task-grant suppression rule (kept out of
// `snap` so they don't widen the no-taskId `chatChips` assertions above — a
// plain chat query with taskId='' would legitimately show them as chips too).
const chatPlusTaskRead = { id: 'f10', name: 'chat-plus-taskread', path: '/data/ctr', exists: true, grants: [
  { profile: PID, chat_id: CHAT, mode: 'read' },                  // chat grant,
  { profile: PID, chat_id: '', task_id: TASK, mode: 'read' },     // covered by a real task grant -> not a chip
] }
const chatPlusTaskNone = { id: 'f11', name: 'chat-plus-tasknone', path: '/data/ctn', exists: true, grants: [
  { profile: PID, chat_id: CHAT, mode: 'read' },                  // chat grant,
  { profile: PID, chat_id: '', task_id: TASK, mode: 'none' },     // task-none is a block, not a cover -> still a chip
] }

test('chatChips: only chat-ONLY folders (chat grant, no profile grant behind it)', () => {
  const chips = chatChips(snap, PID, CHAT)
  // f1 is chat-only; f3 has a profile grant (its override belongs in the note);
  // f6 is blocked (none). So only f1 renders as a removable chip.
  assert.deepEqual(chips.map((c) => c.id).sort(), ['f1'])
  const media = chips.find((c) => c.id === 'f1')
  assert.equal(media.name, 'media')
  assert.equal(media.path, '/data/media')
  assert.equal(media.mode, 'read')
  assert.equal(media.exists, true)
})

test('chatChips: a chat-scoped `none` block never renders as a chip', () => {
  assert.deepEqual(chatChips([snap[5]], PID, CHAT), [])
})

test('chatChips: exists defaults true when the field is absent', () => {
  const chips = chatChips([{ id: 'g', name: 'g', path: '/g', grants: [{ profile: PID, chat_id: CHAT, mode: 'read' }] }], PID, CHAT)
  assert.equal(chips[0].exists, true)
})

test('chatChips: empty / missing input is safe', () => {
  assert.deepEqual(chatChips([], PID, CHAT), [])
  assert.deepEqual(chatChips(undefined, PID, CHAT), [])
})

test('inheritedCount: profile-reachable folders, minus blocked ones', () => {
  // f2 (profile-only) and f3 (profile + widening override) reach the chat; f6 is
  // blocked by a `none` override; f1 (chat-only), f4/f5 (other chat / profile).
  // f7/f8 need a taskId to enter the picture, so with taskId='' they don't count
  // (f7 has no profile grant; f8's profile grant is unaffected by a task block
  // when no task is in play).
  assert.equal(inheritedCount(snap, PID, CHAT), 3)
})

test('addPlan: chat-only grant -> already a chip', () => {
  assert.deepEqual(addPlan(snap[0], PID, CHAT), { status: 'exists' })
})

test('addPlan: profile grant already covers -> covered', () => {
  assert.deepEqual(addPlan(snap[1], PID, CHAT), { status: 'covered', name: 'docs' })
})

test('addPlan: a chat-blocked profile folder -> unblock', () => {
  assert.deepEqual(addPlan(snap[5], PID, CHAT), { status: 'unblock', id: 'f6', name: 'blocked' })
})

test('addPlan: no covering grant -> grant this folder', () => {
  const fresh = { id: 'f9', name: 'new', path: '/data/new', grants: [] }
  assert.deepEqual(addPlan(fresh, PID, CHAT), { status: 'grant', id: 'f9' })
})

test('addPlan: another chat/profile grant does not cover this chat', () => {
  assert.deepEqual(addPlan(snap[3], PID, CHAT), { status: 'grant', id: 'f4' })
  assert.deepEqual(addPlan(snap[4], PID, CHAT), { status: 'grant', id: 'f5' })
})

test('task grants are inherited, not chips, in a run chat', () => {
  assert.equal(chatChips(snap, PID, CHAT, TASK).some((c) => c.id === 'f7'), false)
  assert.ok(inheritedCount(snap, PID, CHAT, TASK) >= 1)
})

test('task-none hides the profile folder from the run chat inherited note', () => {
  // f8 is visible without the task, hidden with it; f7 the other way around —
  // check each in isolation to pin the exact behavior.
  assert.equal(inheritedCount([snap.find((f) => f.id === 'f8')], PID, CHAT, TASK), 0)
  assert.equal(inheritedCount([snap.find((f) => f.id === 'f7')], PID, CHAT, TASK), 1)
})

test('addPlan treats a task-covered folder as covered', () => {
  const plan = addPlan(snap.find((f) => f.id === 'f7'), PID, CHAT, TASK)
  assert.equal(plan.status, 'covered')
})

test('chatChips: a real (non-none) task grant behind a chat grant suppresses the chip', () => {
  // chat-read + a same-task read grant: access works via chat > task, but the
  // task grant is a genuine cover, so it belongs in the note, not as a chip.
  assert.equal(chatChips([chatPlusTaskRead], PID, CHAT, TASK).some((c) => c.id === 'f10'), false)
})

test('chatChips: a task `none` grant behind a chat grant does NOT suppress the chip', () => {
  // chat-read + a same-task `none` grant: the task grant blocks (not covers),
  // so it must not hide the chat chip — regression for the suppression-clause
  // bug where any taskGrant() truthy value (including `none`) hid the chip.
  const chips = chatChips([chatPlusTaskNone], PID, CHAT, TASK)
  const chip = chips.find((c) => c.id === 'f11')
  assert.ok(chip, 'f11 should render as a chip')
  assert.equal(chip.mode, 'read')
})

test('legacy grants without task_id stay profile-scoped', () => {
  // f2 (profile grant, no task_id key at all) stays inherited and never becomes
  // a chip, whether or not a taskId is passed.
  assert.equal(chatChips(snap, PID, CHAT, TASK).some((c) => c.id === 'f2'), false)
  assert.equal(inheritedCount([snap.find((f) => f.id === 'f2')], PID, CHAT, TASK), 1)
})
