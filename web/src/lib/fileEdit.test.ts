import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseEtag, saveErrorMessage, isConflict } from './fileEdit.ts'

test('parseEtag: strips surrounding quotes to the raw token', () => {
  assert.equal(parseEtag('"abc123"'), 'abc123')
})

test('parseEtag: strips a weak W/ prefix as well as quotes', () => {
  assert.equal(parseEtag('W/"abc123"'), 'abc123')
})

test('parseEtag: leaves an already-bare token untouched', () => {
  assert.equal(parseEtag('abc123'), 'abc123')
})

test('parseEtag: trims surrounding whitespace', () => {
  assert.equal(parseEtag('  "abc123"  '), 'abc123')
})

test('parseEtag: absent header yields null', () => {
  assert.equal(parseEtag(null), null)
  assert.equal(parseEtag(undefined), null)
  assert.equal(parseEtag(''), null)
})

test('isConflict: a 409 is the stale-token clash', () => {
  assert.equal(isConflict({ status: 409 }), true)
})

test('isConflict: other statuses and non-errors are not conflicts', () => {
  assert.equal(isConflict({ status: 404 }), false)
  assert.equal(isConflict({ status: 400 }), false)
  assert.equal(isConflict(new Error('network down')), false)
  assert.equal(isConflict(null), false)
  assert.equal(isConflict(undefined), false)
})

test('saveErrorMessage: 404 reads as the file being gone', () => {
  assert.match(saveErrorMessage({ status: 404 }), /no longer exists|not found|gone/i)
})

test('saveErrorMessage: 400 reads as an invalid path', () => {
  assert.match(saveErrorMessage({ status: 400 }), /invalid/i)
})

test('saveErrorMessage: 413 reads as the file being too large', () => {
  assert.match(saveErrorMessage({ status: 413 }), /too large/i)
})

test('saveErrorMessage: prefers a server-supplied error body message', () => {
  assert.equal(saveErrorMessage({ status: 400, body: { error: 'invalid path' } }), 'invalid path')
})

test('saveErrorMessage: falls back to a generic message for other failures', () => {
  const msg = saveErrorMessage({ status: 500 })
  assert.ok(msg && typeof msg === 'string')
  assert.match(msg, /save/i)
})

test('saveErrorMessage: handles a bare Error with a message and no status', () => {
  assert.match(saveErrorMessage(new Error('network down')), /network down|save/i)
})
