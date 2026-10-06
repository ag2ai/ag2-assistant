import type { ThreadItem } from '../schemas/events.ts'
import type { CardInstanceSaveRequest } from '../schemas/card_instance.ts'
import type { A2UIData } from './a2ui.ts'

export const INSTANCE_SUFFIX = '.card-instance.yaml'
type RenderedMessage = Extract<ThreadItem, { kind: 'a2ui' }>

export function instanceFilename(title: string, directory: string, paths: string[]): string {
  const stem = title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').slice(0, 48).replace(/-$/g, '') || 'card'
  const occupied = new Set(paths)
  let filename = stem + INSTANCE_SUFFIX
  let number = 2
  while (occupied.has(directory ? directory + '/' + filename : filename)) filename = stem + '-' + number++ + INSTANCE_SUFFIX
  return filename
}

export function captureInstance(item: RenderedMessage, data: A2UIData, path: string, requestId: string): CardInstanceSaveRequest {
  const components = item.components?.length ? item.components : item.component._components || [item.component]
  return JSON.parse(JSON.stringify({
    surface_id: item.surfaceId, path, request_id: requestId,
    message: {
      version: item.version || 'v1.0', catalog_id: item.catalogId || 'https://ag2.ai/assistant/a2ui/catalog.json',
      component: { ...item.component, _components: components }, data,
      title: item.title || '', intent: item.intent || '',
    },
  })) as CardInstanceSaveRequest
}
