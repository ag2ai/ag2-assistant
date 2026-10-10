import { CardSource, CardSourceEvent } from '../schemas/card_source.ts'
import type { ThreadItem, WireEvent } from '../schemas/events.ts'

type Surface = Extract<ThreadItem, { kind: 'a2ui' }>
export function cardSources(data: Record<string, unknown>): Record<string, CardSource> {
  const raw = data._sources
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  return Object.fromEntries(Object.entries(raw).flatMap(([name, value]) => {
    const parsed = CardSource.safeParse(value)
    return parsed.success ? [[name, parsed.data]] : []
  }))
}

export function projectSourceEvent(item: Surface, wire: WireEvent, path = ''): boolean {
  if (!wire.type.endsWith('.CardSourceUpdated')) return false
  const event = CardSourceEvent.parse(wire.data)
  if (event.path !== path || event.surface_id !== item.surfaceId) return false
  item.sourceStates = { ...item.sourceStates, [event.source_id]: event }
  if (event.status === 'updated' || event.status === 'configured') item.data = event.data
  return true
}
