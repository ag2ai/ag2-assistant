import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatStatusTime } from './chatStatusTime.ts'
import type { ChatSeen } from '../schemas/chat.ts'

const chat: ChatSeen = {
  status: 'waiting', status_version: 'turn', status_updated: 100,
  turn_started_at: 80, waiting_until: 400, unread: false,
}

test('waiting counts down to the original deadline across reloads and stops at zero', () => {
  assert.equal(chatStatusTime(chat, 100), '5:00')
  assert.equal(chatStatusTime(chat, 100.1), '5:00')
  assert.equal(chatStatusTime(chat, 101), '4:59')
  assert.equal(chatStatusTime({ ...chat }, 340.1), '1:00')
  assert.equal(chatStatusTime(chat, 399.5), '0:01')
  assert.equal(chatStatusTime(chat, 410), '0:00')
})

test('running seconds retain the Turn start across HITL and unknown deadlines remain unknown', () => {
  assert.equal(chatStatusTime({ ...chat, status: 'running' }, 340), '260s')
  assert.equal(chatStatusTime({ ...chat, status: 'running' }, 79), '0s')
  assert.equal(chatStatusTime({ ...chat, status: 'running', turn_started_at: 0 }, 100), '—')
  assert.equal(chatStatusTime({ ...chat, waiting_until: null }, 100), '—')
  assert.equal(chatStatusTime({ ...chat, status: null }, 100), '')
})
