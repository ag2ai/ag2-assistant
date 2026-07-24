let _seq = 0
const nextId = () => `a2ui-${Date.now()}-${++_seq}`

export const BETA_CATALOG_ID = 'https://ag2.ai/assistant/a2ui/catalog.json'

function pointerParts(path) {
  return String(path || '').replace(/^\//, '').split('/').filter(Boolean)
    .map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'))
}

// Resolve the literal-or-JSON-Pointer values used by the Basic Catalog.
export function a2uiValue(value, data = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.path !== 'string') {
    return value
  }
  return pointerParts(value.path).reduce((current, part) => current?.[part], data)
}

// Apply a client-side input update without mutating the durable surface payload.
export function withA2UIValue(data = {}, path, value) {
  const parts = pointerParts(path)
  if (!parts.length) return value && typeof value === 'object' ? { ...value } : { value }
  const next = { ...data }
  let target = next
  let source = data
  for (const part of parts.slice(0, -1)) {
    const child = source?.[part]
    target[part] = child && typeof child === 'object' && !Array.isArray(child) ? { ...child } : {}
    target = target[part]
    source = child
  }
  target[parts.at(-1)] = value
  return next
}

// ── A2UI payload in the model's text ────────────────────────────────────────
// The model authors a surface by writing A2UI messages into its reply text (either
// wrapped in <a2ui-json>…</a2ui-json> or as a bare JSON array/objects). The backend
// strips them from the FINAL message, but the chunks stream through raw — so the
// chat has to hide the payload itself while it is being typed.

const A2UI_KEYS = ['createSurface', 'updateComponents', 'updateDataModel', 'deleteSurface']
const OPEN_TAG = '<a2ui-json>'
const CLOSE_TAG = '</a2ui-json>'

// Openings a streaming A2UI payload can start with. A fragment counts as "an A2UI
// payload mid-flight" if it is a prefix of one of these, or has already grown past it.
const HEADS = ['version', ...A2UI_KEYS].flatMap((k) => [`[{"${k}"`, `{"${k}"`])

// End of the JSON value opened at `start`, or -1 if it hasn't been closed yet.
function matchingJsonEnd(text, start) {
  const open = text[start]
  const close = open === '[' ? ']' : '}'
  let depth = 0
  let inString = false
  let escaped = false
  for (let i = start; i < text.length; i++) {
    const ch = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') inString = true
    else if (ch === open) depth++
    else if (ch === close && --depth === 0) return i + 1
  }
  return -1
}

function isA2UIPayload(value) {
  const messages = Array.isArray(value) ? value : [value]
  return messages.some(
    (m) => m && typeof m === 'object' && A2UI_KEYS.some((k) => k in m)
  )
}

// An unterminated JSON fragment: is this the beginning of an A2UI payload (hide it
// and show the composing indicator) or just prose the user should see?
function isA2UIPrefix(fragment) {
  if (A2UI_KEYS.some((k) => fragment.includes(k))) return true
  const compact = fragment.replace(/\s+/g, '')
  return HEADS.some((h) => compact.startsWith(h) || h.startsWith(compact))
}

/** Split an agent message into the prose to render and whether an A2UI payload is
 *  still streaming. Complete payloads are removed (the surface renders as its own
 *  item); a partial one sets `composing` so the UI can show a placeholder instead
 *  of a wall of half-typed JSON. */
export function splitA2UIText(text) {
  const source = text || ''
  let out = ''
  let composing = false
  let i = 0

  while (i < source.length) {
    const tagAt = source.indexOf(OPEN_TAG, i)
    const bracketAt = source.slice(i).search(/[[{]/)
    const jsonAt = bracketAt < 0 ? -1 : i + bracketAt
    const start = tagAt >= 0 && (jsonAt < 0 || tagAt < jsonAt) ? tagAt : jsonAt
    if (start < 0) {
      out += source.slice(i)
      break
    }
    out += source.slice(i, start)

    if (start === tagAt) {
      const close = source.indexOf(CLOSE_TAG, start)
      if (close < 0) {
        composing = true // the wrapped payload is still being written
        break
      }
      i = close + CLOSE_TAG.length
      continue
    }

    const end = matchingJsonEnd(source, start)
    if (end < 0) {
      // Unterminated — either an A2UI payload mid-flight, or ordinary text.
      if (isA2UIPrefix(source.slice(start))) composing = true
      else out += source.slice(start)
      break
    }
    const candidate = source.slice(start, end)
    let parsed
    try {
      parsed = JSON.parse(candidate)
    } catch {}
    if (parsed !== undefined && isA2UIPayload(parsed)) {
      i = end
      continue
    }
    out += candidate
    i = end
  }

  return { text: out.replace(/\n{3,}/g, '\n\n').trim(), composing }
}

// The surface id is usually emitted near the start of an A2UI operation, while
// the component tree may still be streaming. It lets an existing canvas own its
// loading state instead of adding a second placeholder to the thread.
export function a2uiComposingSurfaceId(text) {
  const { composing } = splitA2UIText(text)
  if (!composing) return null
  const match = String(text || '').match(/"(?:createSurface|updateComponents|updateDataModel)"\s*:\s*\{[^}]*"surfaceId"\s*:\s*"([^"\\]+)"/)
  return match?.[1] || null
}

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
  if (['column', 'row', 'list', 'card', 'text'].includes(k)) return 'Interactive view'
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
      title: 'Interactive view',
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
