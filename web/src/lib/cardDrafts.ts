import type { ThreadItem } from '../schemas/events.ts'

export function currentDraft(items: ThreadItem[], item: Extract<ThreadItem, { kind: 'a2ui' }>): boolean {
  const draft = item.draft
  return !!draft && !items.some(entry => entry.kind === 'a2ui' && entry.draft?.id === draft.id && entry.draft.version > draft.version)
}

export function cardFilename(name: string): string {
  return (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48).replace(/-$/g, '') || 'card') + '.card.yaml'
}
