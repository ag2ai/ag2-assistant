import type { ChatRow, ChatSeen, ChatStatus } from '../schemas/chat.ts'
import type { ThreadItem, WireEvent } from '../schemas/events.ts'

export const CHAT_STATUS: Record<ChatStatus, { icon: string; label: string }> = {
  running: { icon: 'spinner', label: 'Running' },
  completed: { icon: 'check', label: 'Completed · unread' },
  error: { icon: 'alert-triangle', label: 'Error · unread' },
  waiting: { icon: 'help-circle', label: 'Waiting for reply' },
}

export function pendingChatDeadlines(items: readonly ThreadItem[], started: number): (number | null)[] {
  return items.flatMap((item) => item.kind === 'inquiry' && !item.resolved && (item.at ?? 0) >= started
    ? [item.expiresAt ?? null] : [])
}

export function chatEventStatus(row: ChatRow, event: WireEvent, pending: readonly (number | null)[]): ChatSeen | null {
  const type = event.type.split('.').pop()
  const data = event.data
  let status = row.status
  let version = row.status_version
  let unread = row.unread
  let started = row.turn_started_at
  let waiting = row.waiting_until
  const at = Number(data.created_at) || row.status_updated
  switch (type) {
    case 'TurnStarted':
      status = 'running'; version = String(data.turn_id); unread = false
      started = at; waiting = null
      break
    case 'TurnCompleted':
      status = 'completed'; version = String(data.turn_id); unread = true
      waiting = null
      break
    case 'TurnFailed':
      if (!version) return null
      status = 'error'; unread = true
      waiting = null
      break
    case 'TurnCancelled':
      status = null; version = ''; unread = false
      waiting = null
      break
    case 'InquiryRaised':
      status = 'waiting'; unread = false
      waiting = earliestDeadline(pending)
      break
    case 'InquiryAnswered':
      if (status !== 'waiting') return null
      status = pending.length ? 'waiting' : version ? 'running' : null; unread = false
      waiting = earliestDeadline(pending)
      break
    case 'ChatSeen':
      if (data.turn_id !== version || (status !== 'completed' && status !== 'error')) return null
      status = null; unread = false
      break
    default:
      return null
  }
  return { status, status_version: version, status_updated: at, turn_started_at: started, waiting_until: waiting, unread }
}

function earliestDeadline(pending: readonly (number | null)[]): number | null {
  const deadlines = pending.filter((deadline): deadline is number => deadline !== null)
  return deadlines.length ? Math.min(...deadlines) : null
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
      ? { ...row, status: previous.status, status_version: previous.status_version, status_updated: previous.status_updated, turn_started_at: previous.turn_started_at, waiting_until: previous.waiting_until, unread: previous.unread }
      : row
    if (next.unread && seen.get(next.chat_id) === next.status_version) next = { ...next, status: null, unread: false }
    return next
  })]
}
