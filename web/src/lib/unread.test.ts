import assert from 'node:assert/strict'
import { test } from 'node:test'
import { badgeText, unreadRuns } from './unread.ts'

test('unread runs add up across tasks', () => {
  assert.equal(unreadRuns([{ unread: 2 }, { unread: 0 }, { unread: 3 }]), 5)
  assert.equal(unreadRuns([]), 0)
})

test('a badge caps at 99+', () => {
  assert.equal(badgeText(5), '5')
  assert.equal(badgeText(120), '99+')
})
