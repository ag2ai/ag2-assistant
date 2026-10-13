import type { ChatRow, ChatSeen, ChatStatus } from '../schemas/chat.ts'
import type { WireEvent } from '../schemas/events.ts'

export const CHAT_STATUS: Record<ChatStatus, { icon: string; label: string }> = {
  running: { icon: 'spinner', label: 'Running' },
  completed: { icon: 'check', label: 'Completed · unread' },
  error: { icon: 'alert-triangle', label: 'Error · unread' },
  waiting: { icon: 'help-circle', label: 'Waiting for reply' },
}

export function chatEventStatus(row: ChatRow, event: WireEvent, pending: boolean): ChatSeen | null {
  const type = event.type.split('.').pop()
  const data = event.data
  let status = row.status
  let version = row.status_version
  let unread = row.unread
  switch (type) {
    case 'TurnStarted':
      status = 'running'; version = String(data.turn_id); unread = false
      break
    case 'TurnCompleted':
      status = 'completed'; version = String(data.turn_id); unread = true
      break
    case 'TurnFailed':
      if (!version) return null
      status = 'error'; unread = true
      break
    case 'TurnCancelled':
      status = null; version = ''; unread = false
      break
    case 'InquiryRaised':
      status = 'waiting'; unread = false
      break
    case 'InquiryAnswered':
      if (status !== 'waiting') return null
      status = pending ? 'waiting' : version ? 'running' : null; unread = false
      break
    case 'ChatSeen':
      if (data.turn_id !== version || (status !== 'completed' && status !== 'error')) return null
      status = null; unread = false
      break
    default:
      return null
  }
  return { status, status_version: version, status_updated: Number(data.created_at) || row.status_updated, unread }
}

export function mergeChatStatus(row: ChatRow, snapshot: ChatSeen): ChatRow {
  return snapshot.status_updated >= row.status_updated ? { ...row, ...snapshot } : row
}

export function mergeChatRows(local: ChatRow[], remote: ChatRow[], seen: ReadonlyMap<string, string>): ChatRow[] {
  const byId = new Map(local.map((row) => [row.chat_id, row]))
  const ids = new Set(remote.map((row) => row.chat_id))
  return [...local.filter((row) => !ids.has(row.chat_id)), ...remote.map((row) => {
    const previous = byId.get(row.chat_id)
    let next = previous && previous.status_updated > row.status_updated
      ? { ...row, status: previous.status, status_version: previous.status_version, status_updated: previous.status_updated, unread: previous.unread }
      : row
    if (next.unread && seen.get(next.chat_id) === next.status_version) next = { ...next, status: null, unread: false }
    return next
  })]
}
