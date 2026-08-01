import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  NO_CHAT_MODEL,
  openedChat,
  loadedChat,
  chooseModel,
  sentFirstMessage,
  switcherSelection,
} from './chatModel.js'

// GET /api/p/{pid}/chats/{id} — the shape the composer's switcher reads.
const doc = (messages, model, effective) => ({
  chat_id: 'c1',
  messages,
  model,
  effective_model: effective,
})
const TURN = [{ role: 'user', text: 'hi' }, { role: 'agent', text: 'yo' }]

// An existing Chat that inherits, with GPT Active install-wide.
const inheriting = loadedChat(openedChat('c1'), 'c1', doc(TURN, '', 'gpt')).state
// The same Chat, overridden to Opus.
const overridden = loadedChat(openedChat('c1'), 'c1', doc(TURN, 'opus', 'opus')).state
// A Chat the user has opened but never sent to.
const unborn = loadedChat(openedChat('c2'), 'c2', doc([], '', 'gpt')).state

test('choosing a model on an open Chat patches that Chat and nothing else', () => {
  const { state, patch } = chooseModel(inheriting, 'opus')
  // The only write is the Chat's own patch — no install-wide "use this model" call.
  assert.equal(patch, 'opus')
  assert.equal(state.chatId, 'c1')
  assert.equal(state.override, 'opus')
  assert.equal(state.pending, null)
})

test('"Use default" patches the empty string, which clears the override', () => {
  const { state, patch } = chooseModel(overridden, '')
  assert.equal(patch, '')
  assert.equal(state.override, '')
})

test('the closed switcher names the effective model, overridden or inherited', () => {
  assert.equal(switcherSelection(inheriting).activeId, 'gpt')
  assert.equal(switcherSelection(overridden).activeId, 'opus')
})

test('the picker marks "Use default" when inheriting and the config when overridden', () => {
  assert.equal(switcherSelection(inheriting).inherited, true)
  assert.equal(switcherSelection(overridden).inherited, false)
})

test('a cleared override falls back to showing the inherited model', () => {
  // The patch lands, then the reload reports the Chat inheriting the Active model.
  const cleared = loadedChat(chooseModel(overridden, '').state, 'c1', doc(TURN, '', 'gpt')).state
  assert.deepEqual(switcherSelection(cleared), { activeId: 'gpt', inherited: true })
})

test('switching Chats shows each Chat\'s own effective model', () => {
  const other = loadedChat(openedChat('c3'), 'c3', doc(TURN, 'sonnet', 'sonnet')).state
  assert.equal(switcherSelection(overridden).activeId, 'opus')
  assert.equal(switcherSelection(other).activeId, 'sonnet')
  // A read that resolves after the user has moved on never lands on the new Chat —
  // and never patches it either.
  assert.deepEqual(loadedChat(other, 'c1', doc(TURN, 'opus', 'opus')), {
    state: other,
    patch: null,
  })
})

test('on a Chat with no messages yet the choice is held, not patched', () => {
  const { state, patch } = chooseModel(unborn, 'opus')
  assert.equal(patch, null)          // there is no Chat to patch
  assert.equal(state.pending, 'opus')
  assert.deepEqual(switcherSelection(state), { activeId: 'opus', inherited: false })
})

test('the held choice is applied by the message that creates the Chat', () => {
  const held = chooseModel(unborn, 'opus').state
  const { state, model } = sentFirstMessage(held)
  assert.equal(model, 'opus')        // rides the turn — the server records it
  assert.equal(state.override, 'opus')
  assert.equal(state.pending, null)
  assert.equal(state.exists, true)
  // A second message carries nothing: the Chat has its own override now.
  assert.equal(sentFirstMessage(state).model, '')
})

test('"Use default" before the first message sends no model and stays inheriting', () => {
  const held = chooseModel(chooseModel(unborn, 'opus').state, '').state
  assert.deepEqual(switcherSelection(held), { activeId: 'gpt', inherited: true })
  const { state, model } = sentFirstMessage(held)
  assert.equal(model, '')
  assert.equal(state.override, '')
})

test('a pre-send choice is deliberately not persisted — a reload returns to inheriting', () => {
  const held = chooseModel(unborn, 'opus').state
  // Reopening the Chat starts from nothing; only the server's answer fills it in.
  assert.deepEqual(openedChat('c2'), { ...NO_CHAT_MODEL, chatId: 'c2' })
  const reopened = loadedChat(openedChat(held.chatId), 'c2', doc([], '', 'gpt')).state
  assert.deepEqual(switcherSelection(reopened), { activeId: 'gpt', inherited: true })
})

test('a pick made before the read lands is reconciled, never dropped', () => {
  // GET /chats/{id} is still in flight, so whether this Chat exists is UNKNOWN — not
  // "no". Treating unknown as "no" held the pick for the first message, which the
  // server discards on a Chat that already has a transcript: the pick vanished.
  const loading = openedChat('c1')
  assert.equal(loading.exists, null)
  const picked = chooseModel(loading, 'opus')
  assert.equal(picked.patch, null)
  assert.deepEqual(switcherSelection(picked.state), { activeId: 'opus', inherited: false })

  // The read lands and settles it: the Chat exists, so the held pick is its override
  // and is PATCHed now — the read's own (older) `model` never overwrites it.
  const landed = loadedChat(picked.state, 'c1', doc(TURN, '', 'gpt'))
  assert.equal(landed.patch, 'opus')
  assert.equal(landed.state.override, 'opus')
  assert.equal(landed.state.pending, null)
  // …and it does not also ride the next turn: chat_model is not a per-message model.
  assert.equal(sentFirstMessage(landed.state).model, '')
})

test('a pick on a Chat that really has no messages stays ephemeral', () => {
  // The other half of the same read: unknown resolved to "no Chat yet" keeps ticket
  // 02's settled behaviour — held in the page, no PATCH, applied by the first message.
  const picked = chooseModel(openedChat('c2'), 'opus')
  const landed = loadedChat(picked.state, 'c2', doc([], '', 'gpt'))
  assert.equal(landed.patch, null)
  assert.equal(landed.state.pending, 'opus')
  assert.equal(landed.state.exists, false)
  assert.equal(sentFirstMessage(landed.state).model, 'opus')
})

test('an empty read leaves the switcher with nothing to name rather than guessing', () => {
  const blank = loadedChat(openedChat('c9'), 'c9', doc([], '', '')).state
  assert.deepEqual(switcherSelection(blank), { activeId: null, inherited: true })
})
