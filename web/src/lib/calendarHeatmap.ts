import { z } from 'zod'

const DAY = 86_400_000
export const CALENDAR_STATUSES = ['activity', 'analysis', 'pause', 'missed', 'unknown', 'upcoming'] as const
export type CalendarStatus = typeof CALENDAR_STATUSES[number]

function dayTime(value: string): number {
  const time = Date.parse(value + 'T00:00:00Z')
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : NaN
}
const DateKey = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => Number(value.slice(0, 4)) >= 1 && Number.isFinite(dayTime(value)), 'Use a valid ISO calendar date')
const Entry = z.object({
  date: DateKey, count: z.number().int().min(0).max(1_000_000).optional(),
  status: z.enum(CALENDAR_STATUSES).optional(), label: z.string().max(500).optional(),
}).strict()
const Entries = z.array(Entry).max(3660)

export type CalendarDay = {
  date: string; column: number; row: number; status: CalendarStatus | 'outside'
  count?: number; label: string; level: number
}
export type CalendarModel = {
  weeks: number; days: CalendarDay[]; months: { date: string; column: number }[]; error: string
}

export function calendarHeatmap(start: unknown, end: unknown, entries: unknown, weekStart = 'monday'): CalendarModel {
  const empty: CalendarModel = { weeks: 0, days: [], months: [], error: '' }
  const dates = z.tuple([DateKey, DateKey]).safeParse([start, end])
  if (!dates.success) return { ...empty, error: 'Use valid startDate and endDate in YYYY-MM-DD format.' }
  const first = dayTime(dates.data[0]), last = dayTime(dates.data[1])
  const length = (last - first) / DAY + 1
  if (length < 1 || length > 366) return { ...empty, error: 'Calendar range must contain 1–366 days.' }
  const parsed = Entries.safeParse(entries)
  if (!parsed.success) return { ...empty, error: 'Days must be unique dated entries with a valid status and non-negative integer count.' }
  const known = new Map<string, z.infer<typeof Entry>>()
  for (const entry of parsed.data) {
    const time = dayTime(entry.date)
    if (time < first || time > last) continue
    if (known.has(entry.date)) return { ...empty, error: `Duplicate calendar day: ${entry.date}. Aggregate sessions by date.` }
    known.set(entry.date, entry)
  }
  const weekday = weekStart === 'sunday' ? 0 : 1
  const offset = (new Date(first).getUTCDay() - weekday + 7) % 7
  const weeks = Math.ceil((offset + length) / 7)
  const months: CalendarModel['months'] = []
  const days: CalendarDay[] = []
  let month = ''
  for (let index = 0; index < weeks * 7; index++) {
    const time = first + (index - offset) * DAY
    const date = new Date(time).toISOString().split('T')[0]
    const column = Math.floor(index / 7), row = index % 7
    if (time < first || time > last) {
      days.push({ date, column, row, status: 'outside', label: '', level: 0 })
      continue
    }
    if (date.slice(0, 7) !== month) {
      if (months.at(-1)?.column === column) months.pop()
      months.push({ date, column }); month = date.slice(0, 7)
    }
    const entry = known.get(date)
    const status = entry ? (entry.status || (entry.count === undefined ? 'unknown' : entry.count > 0 ? 'activity' : 'missed')) : 'unknown'
    days.push({ date, column, row, status, count: entry?.count, label: entry?.label || '', level: status === 'activity' ? Math.max(1, Math.min(4, entry?.count || 1)) : 0 })
  }
  return { weeks, days, months, error: '' }
}
