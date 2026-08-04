import { nextItemId } from './ids.ts'
import type { ThreadItem } from '../schemas/events.ts'

// An A2UI payload is an untyped dictionary — the catalog, not this module, gives
// it meaning, so every read is guarded rather than declared.
export type A2UIData = Record<string, unknown>

// The one thread item this module owns.
type A2UIItem = Extract<ThreadItem, { kind: 'a2ui' }>

// ── Catalog payloads ────────────────────────────────────────────────────────
// Shapes declared by the backend catalog (assistant/a2ui.py assistant_catalog)
// and, for CodingSession, by assistant/coding/surface.py. AG2 validates every
// message against that catalog, so a schema `required` field is declared
// non-optional here and the rest optional. `additionalProperties` is false, so
// no field is declared that the catalog cannot send.

// One node of a component tree. Fields the renderer reads directly are declared;
// bindable ones stay `unknown` because they arrive either literal or as a
// {path} pointer and must go through a2uiValue(). Anything else reads as unknown.
export type A2UIComponent = {
  [key: string]: unknown
  id?: string
  component?: string
  variant?: string
  fit?: string
  displayStyle?: string
  enableDate?: boolean
  enableTime?: boolean
  steps?: number
  children?: unknown[]
  child?: unknown
  options?: A2UIOption[]
  action?: { event?: { name?: string; context?: unknown } }
  _components?: A2UIComponent[]
}

export type A2UIOption = { value?: unknown; label?: unknown }

export type WeatherRow = { label: string; value: string }

// Result row of a RestaurantFinder surface.
export type PlaceResult = { name: string; detail: string; url?: string }

// An action a Button component submits back to the agent.
export type A2UIAction = { name: string; sourceComponentId?: string; context?: unknown }

// `meta` is back-compat: old surfaces stored "Source · 2h ago" in one field.
export type NewsStory = {
  title: string
  source: string
  published?: string
  category?: string
  summary?: string
  why?: string
  image?: string
  url?: string
  meta?: string
  detail?: string
  text?: string
}

export type MarketQuote = {
  symbol: string
  name: string
  price: number
  changePercent: number
  change?: number
  currency?: string
  exchange?: string
  dayLow?: number
  dayHigh?: number
  spark?: number[]
  state?: string
  note?: string
}

export type DecisionOption = { name: string; tagline?: string; price?: string }
export type DecisionCriterion = { label: string; values: string[]; best?: string }

export type InboxThread = {
  from: string
  subject: string
  when?: string
  gist?: string
  unread?: boolean
  needsReply?: boolean
  url?: string
}

export type AgendaEvent = {
  title: string
  start?: string
  end?: string
  location?: string
  allDay?: boolean
  next?: boolean
  url?: string
  joinUrl?: string
}

export type TaskDeliverable = { description: string; status: string }
export type TaskRow = {
  title: string
  status: string
  id?: string
  schedule?: string
  nextRun?: string
  objective?: string
  progress?: string
  deliverables?: TaskDeliverable[]
  error?: string
}

// CodingSession is synthesized by the backend, not authored by the model.
export type CodingPlanStep = { content: string; status: string }
export type CodingFile = { path: string; status: string; hunks: string; added: number; removed: number }

const isRecord = (v: unknown): v is A2UIData => !!v && typeof v === 'object' && !Array.isArray(v)

let _seq = 0
// Fallback surface id when a message omits one — a surface id is a string.
const nextSurfaceId = () => `a2ui-${Date.now()}-${++_seq}`

export const BETA_CATALOG_ID = 'https://ag2.ai/assistant/a2ui/catalog.json'

function pointerParts(path: unknown): string[] {
  return String(path || '').replace(/^\//, '').split('/').filter(Boolean)
    .map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'))
}

// Resolve the literal-or-JSON-Pointer values used by the Basic Catalog.
export function a2uiValue(value: unknown, data: A2UIData = {}): unknown {
  const ref = value as { path?: unknown } | null
  if (!value || typeof value !== 'object' || Array.isArray(value) || typeof ref?.path !== 'string') {
    return value
  }
  // Indexed through a record view: a pointer may also walk arrays and strings.
  return pointerParts(ref.path).reduce<unknown>(
    (current, part) => (current == null ? undefined : (current as A2UIData)[part]),
    data,
  )
}

// Apply a client-side input update without mutating the durable surface payload.
export function withA2UIValue(data: A2UIData = {}, path: unknown, value: unknown): A2UIData {
  const parts = pointerParts(path)
  if (!parts.length) return isRecord(value) || Array.isArray(value) ? { ...(value as A2UIData) } : { value }
  const next: A2UIData = { ...data }
  let target: A2UIData = next
  let source: unknown = data
  for (const part of parts.slice(0, -1)) {
    const child = source == null ? undefined : (source as A2UIData)[part]
    const branch: A2UIData = isRecord(child) ? { ...child } : {}
    target[part] = branch
    target = branch
    source = child
  }
  target[parts.at(-1) ?? ''] = value
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
function matchingJsonEnd(text: string, start: number): number {
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

function isA2UIPayload(value: unknown): boolean {
  const messages = Array.isArray(value) ? value : [value]
  return messages.some((m) => isRecord(m) && A2UI_KEYS.some((k) => k in m))
}

// An unterminated JSON fragment: is this the beginning of an A2UI payload (hide it
// and show the composing indicator) or just prose the user should see?
function isA2UIPrefix(fragment: string): boolean {
  if (A2UI_KEYS.some((k) => fragment.includes(k))) return true
  const compact = fragment.replace(/\s+/g, '')
  return HEADS.some((h) => compact.startsWith(h) || h.startsWith(compact))
}

/** Split an agent message into the prose to render and whether an A2UI payload is
 *  still streaming. Complete payloads are removed (the surface renders as its own
 *  item); a partial one sets `composing` so the UI can show a placeholder instead
 *  of a wall of half-typed JSON. */
export function splitA2UIText(text: string | null | undefined): { text: string; composing: boolean } {
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
    let parsed: unknown
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
export function a2uiComposingSurfaceId(text: string | null | undefined): string | null {
  const { composing } = splitA2UIText(text)
  if (!composing) return null
  const match = String(text || '').match(/"(?:createSurface|updateComponents|updateDataModel)"\s*:\s*\{[^}]*"surfaceId"\s*:\s*"([^"\\]+)"/)
  return match?.[1] || null
}

function componentKind(component: A2UIData = {}): unknown {
  return component.component || 'AnswerBrief'
}

// A title lifted from the data model, falling back when it isn't usable text.
const titleOr = (v: unknown, fallback: string): string => (typeof v === 'string' && v ? v : fallback)

function itemTitle(kind: unknown, data: A2UIData = {}): string {
  const k = String(kind || '').toLowerCase()
  if (k === 'weatherpanel') return 'Weather view'
  if (k === 'decisionmatrix') return titleOr(data.topic, 'Decision')
  if (k === 'taskprogress') return titleOr(data.title, 'Task status')
  if (k === 'agendacard') return titleOr(data.title, 'Agenda')
  if (k === 'inboxbrief') return titleOr(data.title, 'Inbox brief')
  if (k === 'newsdigest') return 'News digest'
  if (k === 'restaurantfinder') return 'Open places'
  if (k === 'taskplan') return 'Task setup'
  if (k === 'checklist') return titleOr(data.title, 'Checklist')
  if (['column', 'row', 'list', 'card', 'text'].includes(k)) return 'Interactive view'
  return 'Structured answer'
}

function dataFromComponent(component: A2UIData = {}, existing: A2UIData = {}): A2UIData {
  const kind = componentKind(component)
  const data: A2UIData = { ...existing }
  for (const [key, value] of Object.entries(component)) {
    if (!['id', 'component', 'accessibility', '_components'].includes(key)) data[key] = value
  }
  if (!data.sections && String(kind).toLowerCase() === 'answerbrief') data.sections = []
  return data
}

function ensureSurface(
  items: ThreadItem[],
  surfaceId: string,
  catalogId: string | undefined,
  version: string,
): A2UIItem {
  let item = items.find((i): i is A2UIItem => i.kind === 'a2ui' && i.surfaceId === surfaceId)
  if (!item) {
    item = {
      id: nextItemId(),
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

export function applyA2UIMessage(items: ThreadItem[], message: unknown): A2UIItem | null {
  if (!isRecord(message)) return null
  const version = str(message.version) || 'v1.0'
  if (isRecord(message.createSurface)) {
    const s = message.createSurface
    const item = ensureSurface(items, str(s.surfaceId) || nextSurfaceId(), str(s.catalogId) || BETA_CATALOG_ID, version)
    record(item).push(message)
    return item
  }
  if (isRecord(message.updateComponents)) {
    const u = message.updateComponents
    const item = ensureSurface(items, str(u.surfaceId) || nextSurfaceId(), undefined, version)
    const components = asComponents(u.components)
    item.components = components
    const found = components.find((c) => c.id === 'root') ?? components[0]
    const root = asComponent(found)
    item.component = root
    item.data = dataFromComponent(root, item.data)
    item.title = itemTitle(componentKind(root), item.data)
    record(item).push(message)
    return item
  }
  if (isRecord(message.updateDataModel)) {
    const u = message.updateDataModel
    const item = ensureSurface(items, str(u.surfaceId) || nextSurfaceId(), undefined, version)
    const path = str(u.path)
    if (!path || path === '/') item.data = isRecord(u.value) ? u.value : { value: u.value }
    else item.data[path.replace(/^\//, '')] = u.value
    record(item).push(message)
    return item
  }
  if (isRecord(message.deleteSurface)) {
    const id = str(message.deleteSurface.surfaceId)
    const idx = items.findIndex((i) => i.kind === 'a2ui' && i.surfaceId === id)
    if (idx >= 0) items.splice(idx, 1)
  }
  return null
}

// A payload field read as text; anything else reads as absent.
export const str = (v: unknown): string => (typeof v === 'string' ? v : '')

// Rows of a catalog array field. AG2 validates element shape against the catalog
// before the surface reaches the client, so the element type is asserted once
// here instead of being re-guarded at every read in the renderers.
export function rows<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value.filter(Boolean) as T[]) : []
}

// The same assertion for a single component node, and for a node list.
export const asComponent = (value: unknown): A2UIComponent => (isRecord(value) ? (value as A2UIComponent) : {})
export const asComponents = (value: unknown): A2UIComponent[] => rows<A2UIComponent>(value)

// The data-model path a bindable field points at; '' when it holds a literal.
export const bindingPath = (value: unknown): string =>
  isRecord(value) && typeof value.path === 'string' ? value.path : ''

// The surface's message log. A surface first created by an A2UISurface event has
// none, and pushing into it used to throw.
const record = (item: A2UIItem): unknown[] => (item.messages ??= [])
