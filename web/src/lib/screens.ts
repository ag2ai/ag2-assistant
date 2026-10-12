import type { Screen } from '../schemas/screen.ts'
import { CardSourceEvent } from '../schemas/card_source.ts'
import type { WireEvent } from '../schemas/events.ts'

export function projectScreenEvent(screen: Screen, wire: WireEvent): Record<string, CardSourceEvent> {
  if (!wire.type.endsWith('.CardSourceUpdated')) return {}
  const parsed = CardSourceEvent.safeParse(wire.data)
  if (!parsed.success) return {}
  const event = parsed.data
  const states: Record<string, CardSourceEvent> = {}
  for (const [id, target] of Object.entries(screen.sources)) {
    if (target.path !== event.path || target.surface_id !== event.surface_id) continue
    if (target.source_id === event.source_id) states[id] = event
    if (event.status !== 'updated' && event.status !== 'configured') continue
    const cards = screen.message.data._cards as Record<string, unknown>
    cards[target.slot] = event.data
    const sources = event.data._sources as Record<string, Record<string, unknown>> | undefined
    if (sources?.[target.source_id]) target.source = sources[target.source_id]
  }
  return states
}
