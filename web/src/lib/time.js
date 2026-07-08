// Shared time formatting. Accepts AG2 event timestamps (Unix SECONDS, float —
// every BaseEvent carries `created_at`) OR ISO strings (task-object fields like
// created_at / started_at / ended_at). One place so the drawer, task panel, and
// thread items read the same.

// Normalize to a Date. Numbers are AG2 `created_at` (Unix seconds); strings are
// ISO 8601. Returns null when missing/unparseable.
export function toDate(v) {
  if (v == null || v === '') return null
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v)
  return isNaN(d.getTime()) ? null : d
}

// Absolute wall-clock: time-only when it happened today, else a short date too.
// e.g. "4:52 AM" or "Jun 23, 4:52 AM".
export function fmtClock(v) {
  const d = toDate(v)
  if (!d) return ''
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// Compact relative past: "just now", "5m ago", "2h ago", "yesterday", "3d ago",
// else a short date.
export function fmtAgo(v) {
  const d = toDate(v)
  if (!d) return ''
  const ms = Date.now() - d.getTime()
  if (ms < 0) return 'just now' // tiny clock skew on a fresh event
  const mins = Math.round(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

// Neat, human absolute date-time for a specific moment (e.g. a scheduled run) —
// replaces raw ISO like "2026-06-23T08:15:00+10:00". Day-aware:
//   "Today 8:15 AM", "Tomorrow 8:15 AM", "Yesterday 8:15 AM",
//   "Mon 8:15 AM" (within a week), else "Jun 23, 8:15 AM".
export function fmtDateTime(v) {
  const d = toDate(v)
  if (!d) return ''
  const now = new Date()
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate())
  const dayDiff = Math.round((startOfDay(d) - startOfDay(now)) / 86400000)
  if (dayDiff === 0) return `Today ${time}`
  if (dayDiff === 1) return `Tomorrow ${time}`
  if (dayDiff === -1) return `Yesterday ${time}`
  if (dayDiff > 1 && dayDiff < 7) return `${d.toLocaleDateString([], { weekday: 'short' })} ${time}`
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// Combined inline stamp shown on thread items / the task panel: "4:52 AM · 2h ago".
export function fmtStamp(v) {
  const clock = fmtClock(v)
  const ago = fmtAgo(v)
  return clock && ago ? `${clock} · ${ago}` : clock || ago
}

// Absolute weekday + time — "Mon 2:30 PM". Used for run rows / schedule lines.
export function fmtWhen(v) {
  const d = toDate(v)
  if (!d) return ''
  return d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })
}

// Relative future until a scheduled time — "Next in 3 mins" / "Due now". Recomputed
// on each drawer refresh so it ticks down.
// Compact "time until next run" for the task list — e.g. "in 2h", "in 5m",
// "in 3d". The verbose "Next in …" wording was repetitive down the list; the
// full next-run date still lives in the row's tooltip.
export function fmtNextIn(v) {
  const d = toDate(v)
  if (!d) return ''
  const ms = d.getTime() - Date.now()
  if (ms <= 0) return 'now'
  const mins = Math.round(ms / 60000)
  if (mins < 1) return 'in <1m'
  if (mins < 60) return `in ${mins}m`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `in ${hours}h`
  const days = Math.round(hours / 24)
  if (days < 7) return `in ${days}d`
  const weeks = Math.round(days / 7)
  if (weeks < 5) return `in ${weeks}w`
  return `in ${Math.round(days / 30)}mo`
}
