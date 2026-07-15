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

// Ultra-compact relative past for dense lists (the drawer's chat rows): "now",
// "5m", "2h", "3d", else a short date. Sibling of fmtAgo without the " ago"
// tail — the row has no room for it and the header already frames the day.
export function fmtAgoShort(v) {
  const d = toDate(v)
  if (!d) return ''
  const ms = Date.now() - d.getTime()
  const mins = Math.round(ms / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d`
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

// Stable per-calendar-day key for grouping thread items under date breakpoints.
// Two moments share a key iff they fall on the same local calendar day. Returns
// null when the item has no time yet (live/streaming items before `created_at`
// lands) so no divider is drawn for them.
export function dayKey(v) {
  const d = toDate(v)
  if (!d) return null
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

// Label for a day breakpoint between messages, ChatGPT-style: a relative day name
// ("Today" / "Yesterday") or an absolute date for anything older, ALWAYS followed
// by that day's first-message time — "Today at 5:24 PM", "Yesterday at 11:00 AM",
// "Fri, Jun 26 at 5:24 PM" (localized, e.g. "пт, 26 июн. в 17:24"). The year shows
// only when it isn't the current one. `v` is the first-of-day item's `at`, so the
// time is that first message's time.
export function fmtDay(v) {
  const d = toDate(v)
  if (!d) return ''
  const now = new Date()
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate())
  const dayDiff = Math.round((startOfDay(d) - startOfDay(now)) / 86400000)
  let day
  if (dayDiff === 0) day = 'Today'
  else if (dayDiff === -1) day = 'Yesterday'
  else {
    const opts = { weekday: 'short', month: 'short', day: 'numeric' }
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
    day = d.toLocaleDateString([], opts)
  }
  return `${day} at ${time}`
}

// Date-only day label for a section header (the chats list), sibling to fmtDay
// but WITHOUT the "at TIME" tail — a header groups many rows, each with its own
// time, so a single time would be meaningless. Today's group reads "Recent"
// (friendlier than "Today" for a chat list); "Yesterday" otherwise a relative
// day name is dropped for an absolute date: "Wed, Jul 13" — weekday + month +
// day, with the year appended only when it isn't the current one.
export function fmtDayShort(v) {
  const d = toDate(v)
  if (!d) return ''
  const now = new Date()
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate())
  const dayDiff = Math.round((startOfDay(d) - startOfDay(now)) / 86400000)
  if (dayDiff === 0) return 'Recent'
  if (dayDiff === -1) return 'Yesterday'
  const opts = { weekday: 'short', month: 'short', day: 'numeric' }
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
  return d.toLocaleDateString([], opts)
}

// Interleave day breakpoints through a list of items: each item is tagged with
// `sep` — the divider/header label to render above it when it's the first item
// of a new calendar day, else null. `label` builds that string from the item's
// `at` (defaults to fmtDay, the thread's per-item divider with a time; the chats
// list passes fmtDayShort for date-only section headers). Items without a time
// (`at` missing/blank — a live/streaming bubble before created_at lands, or a
// bare session stub) never start a new day, so they ride under the previous
// header. Pure so the views and their tests share one source of truth.
export function dayRows(items, label = fmtDay) {
  let lastDay = null
  return items.map((item) => {
    const key = dayKey(item.at)
    const sep = key && key !== lastDay ? label(item.at) : null
    if (key) lastDay = key
    return { item, sep }
  })
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
