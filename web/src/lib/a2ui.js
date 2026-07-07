let _seq = 0
const nextId = () => `a2ui-${Date.now()}-${++_seq}`

export const BETA_CATALOG_ID = 'https://ag2.ai/assistant/a2ui/catalog.json'

function componentKind(component = {}) {
  return component.component || 'AnswerBrief'
}

function itemTitle(kind, data = {}) {
  const k = String(kind || '').toLowerCase()
  if (k === 'weatherpanel') return 'Weather view'
  if (k === 'decisionmatrix') return data.topic || 'Decision'
  if (k === 'taskprogress') return data.title || 'Task status'
  if (k === 'agendacard') return data.title || 'Agenda'
  if (k === 'inboxbrief') return data.title || 'Inbox brief'
  if (k === 'newsdigest') return 'News digest'
  if (k === 'restaurantfinder') return 'Open places'
  if (k === 'taskplan') return 'Task setup'
  if (k === 'checklist') return data.title || 'Checklist'
  if (['column', 'row', 'list', 'card', 'text'].includes(k)) return 'Briefing'
  return 'Structured answer'
}

function dataFromComponent(component = {}, existing = {}) {
  const kind = componentKind(component)
  const data = { ...existing }
  for (const [key, value] of Object.entries(component)) {
    if (!['id', 'component', 'accessibility', '_components'].includes(key)) data[key] = value
  }
  if (!data.sections && String(kind).toLowerCase() === 'answerbrief') data.sections = []
  return data
}

function ensureSurface(items, surfaceId, catalogId, version) {
  let item = items.find((i) => i.kind === 'a2ui' && i.surfaceId === surfaceId)
  if (!item) {
    item = {
      id: nextId(),
      kind: 'a2ui',
      version: version || 'v1.0',
      catalogId: catalogId || BETA_CATALOG_ID,
      surfaceId,
      title: 'Briefing',
      intent: '',
      component: {},
      data: {},
      messages: [],
    }
    items.push(item)
  }
  return item
}

export function applyA2UIMessage(items, message) {
  if (!message || typeof message !== 'object') return null
  const version = message.version || 'v1.0'
  if (message.createSurface) {
    const s = message.createSurface
    const item = ensureSurface(items, s.surfaceId || nextId(), s.catalogId || BETA_CATALOG_ID, version)
    item.messages.push(message)
    return item
  }
  if (message.updateComponents) {
    const u = message.updateComponents
    const item = ensureSurface(items, u.surfaceId || nextId(), undefined, version)
    const components = Array.isArray(u.components) ? u.components : []
    item.components = components
    const root = components.find((c) => c.id === 'root') || components[0] || {}
    item.component = root
    item.data = dataFromComponent(root, item.data)
    item.title = itemTitle(componentKind(root), item.data)
    item.messages.push(message)
    return item
  }
  if (message.updateDataModel) {
    const u = message.updateDataModel
    const item = ensureSurface(items, u.surfaceId || nextId(), undefined, version)
    if (!u.path || u.path === '/') item.data = typeof u.value === 'object' && u.value ? u.value : { value: u.value }
    else item.data[u.path.replace(/^\//, '')] = u.value
    item.messages.push(message)
    return item
  }
  if (message.deleteSurface) {
    const id = message.deleteSurface.surfaceId
    const idx = items.findIndex((i) => i.kind === 'a2ui' && i.surfaceId === id)
    if (idx >= 0) items.splice(idx, 1)
  }
  return null
}
