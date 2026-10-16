import type { ChatSeen } from '../schemas/chat.ts'
import { fmtAgoShort } from './time.ts'

export function chatStatusTime(chat: ChatSeen, now: number): string {
  if (chat.status === 'running') {
    return chat.turn_started_at > 0 ? `${Math.max(0, Math.floor(now - chat.turn_started_at))}s` : '—'
  }
  if (chat.status === 'waiting') {
    if (chat.waiting_until === null) return '—'
    const seconds = Math.max(0, Math.ceil(chat.waiting_until - now))
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
  }
  return chat.status ? fmtAgoShort(chat.status_updated) : ''
}
