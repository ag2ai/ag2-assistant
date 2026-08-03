import { writable, type Writable } from 'svelte/store'
import { DEFAULT_RAIL_WIDTH, DEFAULT_DRAWER_WIDTH } from './lib/railWidth.js'
import type {
  ChatRow,
  Inquiry,
  MentionRow,
  Profile,
  RunDetail,
  Task,
  ThreadItem,
  WireEvent,
} from './schemas/index.ts'
// SETTINGS_PAGE — the valid Settings Section ids — lives in the pure route core
// (lib/route.js validates the `#settings=<section>` hash against it). Re-export it
// here so callers keep importing it from the store (SETTINGS_PAGE.MODELS, …).
export { SETTINGS_PAGE } from './lib/route.js'
// settingsOpen and ag2View (whether the AG2 Inspector occupies the rail) are derived
// from the route; they live in router.ts to avoid a module-init cycle, re-exported here.
export { settingsOpen, ag2View } from './router.ts'
import { go } from './router.ts'

// Multi-profile registry (§5.2). `list` mirrors GET /api/profiles; `activeId`
// is the profile the client is currently viewing (persisted separately in
// localStorage via lib/profile.js). Minimal in Phase 1 — Phase 2 builds the
// switcher chips + activity badges on top of this.
export const profiles: Writable<{ list: Profile[]; activeId: string | null }> = writable({
  list: [],
  activeId: null,
})

// Bumped once per in-place profile switch (controller.switchProfile). A
// profile-scoped async write guards on it to drop stale results; the open Settings
// modal watches it to reload the new profile's payload in place.
export const profileEpoch = writable(0)

// A thread is one AG2 stream. 'run' opens a task run, whose stream id carries the
// `task-run:` prefix — hence `chat` alongside `id` (controller.openThread).
export type ThreadKind = 'chat' | 'task' | 'run'

export type Thread = {
  id: string | null
  kind: ThreadKind
  chat: string
  items: ThreadItem[]
  busy: boolean
}

// The active thread: a projection of one AG2 stream. `items` are folded from
// `{type,data}` events (see project.ts). `kind` is 'chat' or 'task'.
export const thread: Writable<Thread> = writable({
  id: null,
  kind: 'chat',
  chat: '',
  items: [],
  busy: false,
})

// Drawer: unified history of chats + tasks, plus the user-writable Files tree.
export const chats: Writable<ChatRow[]> = writable([])
export const tasks: Writable<Task[]> = writable([])
// The active drawer Tab ('chats' | 'tasks' | 'files') is not a store — it is the
// `tab` field of the current route (see router.ts); read $route.tab.

// The open run's durable header data ({id, task_id, task_name, status, …}),
// polled while a run thread is open; null otherwise.
export const runInfo: Writable<RunDetail | null> = writable(null)

// A one-shot request to open a task's edit modal once its page loads. Set by the
// Drawer's task-row "Edit" action (which navigates to /t/{id} first), consumed and
// cleared by TaskPage when the matching task has loaded. Null when no request pending.
export const pendingTaskEdit: Writable<string | null> = writable(null)

// Durable HITL: pending questions/permissions across all tasks, answerable
// anywhere (polled). Survives restarts — backed by the InquiryStore.
export const inquiries: Writable<Inquiry[]> = writable([])

// Google connect modal open/closed.
export const googleOpen = writable(false)

// "Sign in with ChatGPT" (OpenAI Codex subscription) modal open/closed.
export const codexOpen = writable(false)

// Voice picker modal open/closed.
export const voicePickerOpen = writable(false)
// When the voice picker targets a specific named live config, its id (else null for
// the profile's legacy voice setting). Set alongside voicePickerOpen; the picker
// scopes its voices/select/preview to this config and stacks over Settings.
export const voicePickerConfig: Writable<string | null> = writable(null)

// The path-less transient preview: a text-only deliverable body with no on-disk path
// to address, rendered in the rail. { title, text } when open, null when closed.
// (Path-backed previews live in the URL — router.openAsideFile.)
export const viewer: Writable<{ title: string; text: string } | null> = writable(null)

// A one-shot Reveal request: locate a file or directory where it lives in the Files
// tree. `path` is the row to surface; `kind` is 'file' (expand ancestors, scroll the
// file's row) or 'directory' (also expand the directory itself, so its contents show);
// `epoch` bumps on every request so FilesTree re-fires its expand+scroll even for a
// repeat Reveal of the same path. Transient — never persisted, never in the URL (the
// expanded tree shape is session view-state).
export type Reveal = { path: string | null; kind: 'file' | 'directory'; epoch: number }

export const reveal: Writable<Reveal> = writable({ path: null, kind: 'file', epoch: 0 })

// Reveal the given file in the Files tree: record the request (bumping the epoch) and
// switch to the Files Tab. FilesTree reacts — pull a fresh listing, persistently expand
// the file's ancestor Directories, and scroll its row into view. Leaves the preview
// (aside) Active file and the upload-target selection untouched. A blank path is a no-op.
export function revealFile(path: string | null | undefined): void {
  if (!path) return
  reveal.update((r) => ({ path, kind: 'file', epoch: r.epoch + 1 }))
  go('/files')
}

// Open the Thread a "Mentioned in" popover row points at (ADR 0014): a `chat` row
// opens that chat, a `run` row opens the run stream (`task-run:{id}`) as a Thread.
// Navigation only — `go` preserves the hash, so the aside (the previewed file) stays
// open; the aside is orthogonal to the Thread (ADR 0008/0009). A run's `stream_id`
// carries the `task-run:` prefix; strip it to the run id the `/r/{id}` route expects.
export function openThreadRow(row: MentionRow | null | undefined): void {
  if (!row?.stream_id) return
  if (row.kind === 'run') go('/r/' + row.stream_id.replace(/^task-run:/, ''))
  else go('/c/' + row.stream_id)
}

// Reveal (and expand) a Directory in the Files tree: same as revealFile but FilesTree
// also opens the directory itself so its contents are visible. A folder has no preview
// rail (it isn't a file), so a mentioned folder browses here instead (ADR 0012).
export function revealFolder(path: string | null | undefined): void {
  if (!path) return
  reveal.update((r) => ({ path, kind: 'directory', epoch: r.epoch + 1 }))
  go('/files')
}

// (settingsOpen is re-exported from router.ts at the top of this file — it's derived
// from the route. Open it with router.openOverlay('settings', section); close it with
// router.closeOverlay(). The active Section is $route.overlayValue.)

// The active Settings Section is read from the route (`$route.overlayValue`),
// validated against SETTINGS_PAGE by the pure core. Callers deep-link into a
// Section with router.openOverlay('settings', SETTINGS_PAGE.MODELS) — a bad id is
// impossible to mistype (no more 'model' vs 'models' drift). Settings binds each id
// to a label + component. (SETTINGS_PAGE is re-exported from lib/route.js above.)

// "Powered by AG2" architecture-map modal open/closed.
export const poweredByOpen = writable(false)

// App version, seeded from the GET /api/profiles boot payload. Shown in the
// "Powered by AG2" modal footer. Empty until boot completes.
export const appVersion = writable('')

// The AG2 version the app is running on, from the same boot payload. Empty when the
// server couldn't read it, which the footer treats as "just omit it".
export const ag2Version = writable('')

// One buffered event: the wire event plus the arrival stamp and monotonic key the
// Inspector renders and keys its rows on (controller._inspect).
export type InspectorEvent = WireEvent & { _t: number; _id: number }

// A bounded buffer of the raw {type,data} events the current chat's stream
// already delivers — the AG2 Inspector renders it to show the live AG2 events
// behind the UI. Reset when a thread opens (see controller.openThread).
export const inspectorEvents: Writable<InspectorEvent[]> = writable([])

// A localStorage-backed preference (per-device): survives reloads.
function persisted<T>(key: string, initial: T): Writable<T> {
  let v = initial
  try { const s = localStorage.getItem(key); if (s !== null) v = JSON.parse(s) as T } catch {}
  const w = writable<T>(v)
  w.subscribe((val) => { try { localStorage.setItem(key, JSON.stringify(val)) } catch {} })
  return w
}

// Play a chime when the assistant needs your input (HITL). Off by default.
export const soundOnInput = persisted('soundOnInput', false)

// The AG2 Inspector rail's width in px. localStorage-backed view-state, never in the
// URL; applied through clampRailWidth. Separate from the file preview (previewWidth).
export const railWidth = persisted('railWidth', DEFAULT_RAIL_WIDTH)

// The file preview rail's width in px — its own store, reset to default on close.
// localStorage-backed view-state; applied through clampRailWidth.
export const previewWidth = persisted('previewWidth', DEFAULT_RAIL_WIDTH)

// Whether the file preview is expanded to fill the Thread column. localStorage-backed
// view-state, never in the URL (see ADR 0009 amendment). Preview only.
export const previewExpanded = persisted('previewExpanded', false)

// Reset the preview's per-viewing sizing to a docked default width, un-expanded.
// Called from Viewer.onDestroy so any close path resets it; leaves railWidth alone.
export function resetPreviewView(): void {
  previewWidth.set(DEFAULT_RAIL_WIDTH)
  previewExpanded.set(false)
}

// The left drawer's width in px, drag-resized via RailResizer (side="left").
// Same localStorage-backed view-state; always applied through clampDrawerWidth.
export const drawerWidth = persisted('drawerWidth', DEFAULT_DRAWER_WIDTH)

// "AG2 view" (ag2View) is derived from the route and re-exported at the top of this
// file — the Inspector occupies the URL-addressable aside, so it's no longer persisted.

// App-wide animation quality (per-device — the GPU cost is local). Any rich
// surface should honour it; weather panels are the first consumer:
//   'off'   — static content only (weather: emoji glyph, pure HTML), zero GPU
//   'basic' — simple CSS/SVG animation, compositor-cheap (default: kind to GPUs;
//             High is an explicit opt-in from Settings)
//   'high'  — full WebGPU 3D scenes (volumetrics, bloom); consumers fall back
//             to 'basic' on browsers without WebGPU
export type AnimationQuality = 'off' | 'basic' | 'high'

export const animations = persisted<AnimationQuality>('animations', 'basic')

// First-run onboarding overlay open/closed. Opened automatically on first launch
// when this install hasn't completed/dismissed it and no provider key is stored
// (see App.svelte), or via Settings → "Re-run setup". The install-level "have we
// onboarded?" flag lives in the registry (GET /api/profiles `onboarded`, §4.2) —
// set via api.setOnboarded() at the END of the onboarding flow (§5.5).
export const onboardingOpen = writable(false)

// Local user display name seeded by onboarding — greets the user. Kept on-device
// (per-profile focus areas moved server-side into each profile's settings.json,
// where they're injected into the agent's context).
export const profile = persisted('ag2-profile', { name: '' })

// Transient toast/notice: { text } when shown, null when hidden. Used by the
// archived-profile recovery flow (§4.9) — a brief message before the client
// re-resolves to a valid profile. Minimal by design; no queue.
export const notice: Writable<{ text: string } | null> = writable(null)
