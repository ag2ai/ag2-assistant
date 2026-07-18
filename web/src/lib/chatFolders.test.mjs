import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatChips, profileExtraCount, addPlan } from './chatFolders.js'

const PID = 'work'
const CHAT = 'web-abc'

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
]

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

test('profileExtraCount: profile-reachable folders, minus blocked ones', () => {
  // f2 (profile-only) and f3 (profile + widening override) reach the chat; f6 is
  // blocked by a `none` override; f1 (chat-only), f4/f5 (other chat / profile).
  assert.equal(profileExtraCount(snap, PID, CHAT), 2)
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
