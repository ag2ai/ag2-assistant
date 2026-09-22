import { nextItemId } from './ids.ts'
import { fmtAgo, fmtClock, fmtDateTime } from './time.ts'
import type { TimeValue } from './time.ts'
import { safeUrl } from './url.ts'
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
  children?: unknown
  child?: unknown
  when?: unknown
  map?: unknown
  format?: unknown
  options?: A2UIOption[]
  action?: { event?: { name?: string; context?: unknown } }
  _components?: A2UIComponent[]
}

export type A2UIOption = { value?: unknown; label?: unknown }

export type WeatherRow = { label: string; value: string }

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

export type DecisionOption = { name: string; tagline?: string; price?: string }
export type DecisionCriterion = { label: string; values: string[]; best?: string }

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

// A scope is the pointer to the item a repeated row is drawing — '' outside any
// repetition. A path opening with `.` is relative to it; anything else is absolute.
function scoped(path: string, scope: string): string {
  if (!path.startsWith('.')) return path
  const rest = path.replace(/^\.\/?/, '')
  return rest ? `${scope}/${rest}` : scope
}

// Resolve the literal-or-JSON-Pointer values used by the Basic Catalog.
export function a2uiValue(value: unknown, data: A2UIData = {}, scope = ''): unknown {
  const ref = value as { path?: unknown } | null
  if (!value || typeof value !== 'object' || Array.isArray(value) || typeof ref?.path !== 'string') {
    return value
  }
  // Indexed through a record view: a pointer may also walk arrays and strings.
  return pointerParts(scoped(ref.path, scope)).reduce<unknown>(
    (current, part) => (current == null ? undefined : (current as A2UIData)[part]),
    data,
  )
}

// One child a layout draws: the component to render and the data scope it draws in.
export type A2UIChildSlot = { id: string; scope: string }

// The children of a Column, Row or List: an explicit array of ids draws each once,
// a `{componentId, path}` template draws one id per item of the bound array.
export function childSlots(children: unknown, data: A2UIData = {}, scope = ''): A2UIChildSlot[] {
  if (Array.isArray(children)) {
    return children.filter((id): id is string => typeof id === 'string').map((id) => ({ id, scope }))
  }
  if (!isRecord(children)) return []
  const { componentId, path } = children as { componentId?: unknown; path?: unknown }
  if (typeof componentId !== 'string' || typeof path !== 'string') return []
  const items = a2uiValue({ path }, data, scope)
  if (!Array.isArray(items)) return []
  const base = scoped(path, scope)
  const start = Math.max(0, Math.trunc(Number((children as { start?: unknown }).start) || 0))
  return items
    .map((_, index) => ({ id: componentId, scope: `${base}/${index}` }))
    .slice(start)
}

// ── The styling vocabulary (ADR 0028) ───────────────────────────────────────
// A Card names a tone, a format or a label; the renderer resolves the name.

export type A2UITone = 'neutral' | 'muted' | 'accent' | 'positive' | 'negative'

const TONES: readonly string[] = ['neutral', 'muted', 'accent', 'positive', 'negative']

// A bound value put through the Card's own table of value → name. A value the
// table does not name is left as it is.
function named(raw: unknown, map: unknown): unknown {
  const label = isRecord(map) ? map[String(raw)] : undefined
  return label === undefined ? raw : label
}

/** The tone a component is drawn in: a word from the vocabulary, or a binding —
 *  a bound number takes its tone from its sign, and a bound word carrying a `map`
 *  takes the tone that table names for it. */
export function a2uiTone(tone: unknown, data: A2UIData = {}, scope = ''): A2UITone {
  const bound = a2uiValue(tone, data, scope)
  const value = isRecord(tone) ? named(bound, tone.map) : bound
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral'
  }
  const word = str(value)
  return TONES.includes(word) ? (word as A2UITone) : 'neutral'
}

/** Whether a component conditional on its data is drawn. Absent, empty, `false`
 *  and an empty array are nothing to draw for; zero is a value like any other.
 *  A list of conditions is drawn for when any one of them is there. */
export function a2uiPresent(when: unknown, data: A2UIData = {}, scope = ''): boolean {
  if (when === undefined) return true
  if (Array.isArray(when)) return when.some((one) => a2uiPresent(one, data, scope))
  const value = a2uiValue(when, data, scope)
  if (value == null || value === '' || value === false) return false
  return !Array.isArray(value) || value.length > 0
}

// A timestamp is written the way the rest of the app writes one.
const FORMATS: Record<string, (v: TimeValue) => string> = {
  time: fmtClock,
  ago: fmtAgo,
  datetime: fmtDateTime,
}

/** One Text's printed string: its bound value, put through the Card's own label
 *  map or its time format. Nothing bound prints nothing. */
export function a2uiText(component: A2UIComponent, data: A2UIData = {}, scope = ''): string {
  const raw = a2uiValue(component.text, data, scope)
  const format = FORMATS[str(component.format)]
  if (format) return format(raw as TimeValue)
  const value = named(raw, component.map)
  return value == null || value === '' ? '' : String(value)
}

/** One Icon's glyph name: its bound value, put through the Card's own map — so a
 *  row's status field can pick the mark that stands for it. */
export function a2uiIconName(component: A2UIComponent, data: A2UIData = {}, scope = ''): string {
  return str(named(a2uiValue(component.name, data, scope), component.map))
}

// ── Card links (ADR 0008) ───────────────────────────────────────────────────
// A Card names one of the app's own things, or an external page; the shell opens it.

export type A2UILinkKind = 'task' | 'chat' | 'file' | 'folder' | 'url'
export type A2UILink = { kind: A2UILinkKind; value: string }

// The Task and Chat ids the shell has listed; null for a list it has not polled yet.
export type A2UIKnown = { tasks: readonly string[] | null; chats: readonly string[] | null }

// The order a Link's targets are read in; the first one it names is the one it opens.
const LINK_KINDS: readonly A2UILinkKind[] = ['task', 'chat', 'file', 'folder', 'url']

// Whether a named id is gone: a list the shell holds and this is not in it.
const missing = (known: readonly string[] | null, id: string): boolean =>
  known !== null && !known.includes(id)

/** The thing one Link points at, or null when it points at nothing that is there —
 *  a deleted Task, a Chat that is gone, a scheme we will not follow. */
export function a2uiLink(
  component: A2UIComponent,
  data: A2UIData = {},
  scope = '',
  known: A2UIKnown = { tasks: null, chats: null },
): A2UILink | null {
  for (const kind of LINK_KINDS) {
    const value = str(a2uiValue(component[kind], data, scope))
    if (!value) continue
    if (kind === 'task') return missing(known.tasks, value) ? null : { kind, value }
    if (kind === 'chat') return missing(known.chats, value) ? null : { kind, value }
    if (kind === 'url') {
      const safe = safeUrl(value)
      return safe ? { kind, value: safe } : null
    }
    // A path is taken as given: the Files rail reports a file that has gone itself.
    return { kind, value }
  }
  return null
}

export type A2UISpark = { line: string; area: string; endX: string; endY: string }

/** A normalised 0..100 series as an SVG line, its filled area, and the end point,
 *  inset by `pad` within a w×h box. Fewer than two points draw nothing. */
export function sparkPath(values: unknown, w: number, h: number, pad = 3): A2UISpark | null {
  const points = (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite)
  if (points.length < 2) return null
  const x = (i: number) => pad + (i / (points.length - 1)) * (w - pad * 2)
  const y = (v: number) => pad + (1 - Math.max(0, Math.min(100, v)) / 100) * (h - pad * 2)
  const drawn = points.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`)
  const line = `M${drawn.join(' L')}`
  return {
    line,
    area: `${line} L${x(points.length - 1).toFixed(1)},${(h - pad).toFixed(1)} L${x(0).toFixed(1)},${(h - pad).toFixed(1)} Z`,
    endX: x(points.length - 1).toFixed(1),
    endY: y(points[points.length - 1]).toFixed(1),
  }
}

export type A2UIMetric = { value: string; unit: string; delta: string; arrow: string }

// Numbers a Metric prints: grouped, two decimals, so a column of them lines up.
const decimal = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const signed = (n: number) => `${n > 0 ? '+' : ''}${decimal(n)}`

const finiteNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/** One Metric's printable parts: its value, its unit, and its movement written
 *  as a signed absolute change, a signed percent, or both. */
export function metricParts(
  component: A2UIComponent,
  data: A2UIData = {},
  scope = '',
): A2UIMetric {
  const raw = a2uiValue(component.value, data, scope)
  const change = finiteNumber(a2uiValue(component.delta, data, scope))
  const percent = finiteNumber(a2uiValue(component.deltaPercent, data, scope))
  const moved = percent ?? change
  const parts = [
    change == null ? '' : signed(change),
    percent == null ? '' : `${signed(percent)}%`,
  ].filter(Boolean)
  return {
    value: typeof raw === 'number' ? decimal(raw) : str(raw),
    unit: str(a2uiValue(component.unit, data, scope)),
    delta: parts.length === 2 ? `${parts[0]} (${parts[1]})` : parts[0] || '',
    arrow: moved == null || moved === 0 ? '' : moved > 0 ? '▲' : '▼',
  }
}

// The key `part` names in `container`; null when it names nothing writable — an
// array answers only to an index it already holds. Mirrors a2ui.py's `_key_for`.
function writableKey(container: unknown, part: string): string | null {
  if (!Array.isArray(container)) return part
  return /^\d+$/.test(part) && Number(part) < container.length ? part : null
}

// Apply a client-side input update without mutating the durable surface payload.
// A bound array is cloned as an array, so a repeated row writes to its own item.
export function withA2UIValue(data: A2UIData = {}, path: unknown, value: unknown): A2UIData {
  const parts = pointerParts(path)
  if (!parts.length) return isRecord(value) || Array.isArray(value) ? { ...(value as A2UIData) } : { value }
  const next: A2UIData = { ...data }
  let target: A2UIData = next
  let source: unknown = data
  for (const part of parts.slice(0, -1)) {
    const key = writableKey(target, part)
    if (key === null) return next
    const child = source == null ? undefined : (source as A2UIData)[part]
    const branch = Array.isArray(child) ? [...child] : isRecord(child) ? { ...child } : {}
    target[key] = branch
    target = branch as A2UIData
    source = child
  }
  const last = writableKey(target, parts.at(-1) ?? '')
  if (last === null) return next
  target[last] = value
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
  if (k === 'newsdigest') return 'News digest'
  if (['column', 'row', 'list', 'card', 'text'].includes(k)) return titleOr(data.title, 'Interactive view')
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
    else item.data = withA2UIValue(item.data, path, u.value)
    // A surface titled by its data model is retitled when that data arrives.
    if (item.component.component) item.title = itemTitle(componentKind(item.component), item.data)
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

// The context a Button submits: every binding resolved in the scope it was drawn
// in, so a button in a repeated row carries its own item.
export function actionContext(value: unknown, data: A2UIData = {}, scope = ''): unknown {
  if (Array.isArray(value)) return value.map((item) => actionContext(item, data, scope))
  if (!isRecord(value)) return value
  if (typeof value.path === 'string' && Object.keys(value).length === 1) return a2uiValue(value, data, scope)
  return Object.fromEntries(Object.entries(value).map(([k, item]) => [k, actionContext(item, data, scope)]))
}

// The data-model path a bindable field writes to, resolved against the scope it was
// drawn in; '' when it holds a literal.
export const bindingPath = (value: unknown, scope = ''): string =>
  isRecord(value) && typeof value.path === 'string' ? scoped(value.path, scope) : ''

// The surface's message log. A surface first created by an A2UISurface event has
// none, and pushing into it used to throw.
const record = (item: A2UIItem): unknown[] => (item.messages ??= [])
