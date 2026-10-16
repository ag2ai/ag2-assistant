import { foldEvent } from '../project.ts'
import type { ThreadItem, WireEvent } from '../schemas/events.ts'

/** Rebuild loaded events while preserving mounted items and nested traces. */
export function projectHistory(events: WireEvent[], previous: ThreadItem[]): ThreadItem[] {
  const items: ThreadItem[] = []
  for (const event of events) foldEvent(items, event)
  preserveIds(items, previous)
  for (const old of previous) {
    if ((old.kind !== 'user' && old.kind !== 'agent') || (!old.voice && !(old.kind === 'user' && old.queued))) continue
    if (items.some(item => item.id === old.id || (item.kind === old.kind && item.text === old.text))) continue
    const next = previous.slice(previous.indexOf(old) + 1).find(item => items.some(value => value.id === item.id))
    const index = next ? items.findIndex(item => item.id === next.id) : items.length
    items.splice(index, 0, old)
  }
  return items
}

function identity(item: ThreadItem): string {
  if (item.kind === 'a2ui') return 'a2ui:' + item.surfaceId
  if (item.kind === 'inquiry') return 'inquiry:' + item.inquiryId
  if (item.kind === 'subagent') return 'subagent:' + item.taskId
  return item.kind + ':' + (item.at ?? ('text' in item ? item.text : JSON.stringify(item)))
}

function preserveIds(items: ThreadItem[], previous: ThreadItem[]): void {
  const ids = new Map<string, ThreadItem[]>()
  for (const item of previous) {
    const key = identity(item)
    ids.set(key, [...(ids.get(key) ?? []), item])
  }
  for (const item of items) {
    const old = ids.get(identity(item))?.shift()
    if (!old) continue
    item.id = old.id
    if (item.kind === 'subagent' && old.kind === 'subagent') preserveIds(item.items, old.items)
  }
}
