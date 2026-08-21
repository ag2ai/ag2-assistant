// File extension → Viewer render kind (mirror of the tool→card registry).
// html / image / pdf use the browser's native rendering against /api/files/raw —
// the server already sends the right Content-Type, so no libraries are needed.
// md / code / text render in-app; anything unknown is download-only.
import { m } from '../paraglide/messages.js'

// Every render mode the Viewer knows; 'download' means no in-app preview.
export type ViewerKind = 'html' | 'image' | 'pdf' | 'markdown' | 'text' | 'code' | 'download'

const KIND: Record<string, ViewerKind> = {
  html: 'html',
  htm: 'html',
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  gif: 'image',
  webp: 'image',
  svg: 'image',
  bmp: 'image',
  ico: 'image',
  avif: 'image',
  pdf: 'pdf',
  md: 'markdown',
  markdown: 'markdown',
  txt: 'text',
  log: 'text',
  csv: 'text',
  py: 'code',
  js: 'code',
  mjs: 'code',
  ts: 'code',
  jsx: 'code',
  tsx: 'code',
  json: 'code',
  css: 'code',
  scss: 'code',
  yaml: 'code',
  yml: 'code',
  toml: 'code',
  ini: 'code',
  sh: 'code',
  xml: 'code',
  sql: 'code',
  rs: 'code',
  go: 'code',
  java: 'code',
  rb: 'code',
}

// The render mode for a file name (by extension), or 'download' if not previewable.
export function viewerKind(name: string | null | undefined): ViewerKind {
  const ext = ((name || '').split('.').pop() ?? '').toLowerCase()
  return KIND[ext] || 'download'
}

// Whether the Files browser / tool card should offer a "view" affordance.
export const previewable = (name: string | null | undefined): boolean => viewerKind(name) !== 'download'

// Icon name (an Icon.svelte glyph) for a file row, by extension. Resolution order,
// most specific first: exact well-known filename → extension refinements the render
// KIND can't distinguish (csv is 'text', archives are 'download') → the render KIND
// → the generic `file`. Icon-only: never widens what's previewable.
const ICON_BY_NAME: Record<string, string> = {
  dockerfile: 'file-code',
  makefile: 'file-code',
  '.gitignore': 'file-code',
  '.env': 'file-code',
}
const ICON_BY_EXT: Record<string, string> = {
  csv: 'file-spreadsheet',
  tsv: 'file-spreadsheet',
  zip: 'file-archive',
  tar: 'file-archive',
  gz: 'file-archive',
  tgz: 'file-archive',
  bz2: 'file-archive',
  rar: 'file-archive',
  '7z': 'file-archive',
  mp4: 'file-play',
  mov: 'file-play',
  webm: 'file-play',
  mkv: 'file-play',
  avi: 'file-play',
  m4v: 'file-play',
  mp3: 'file-music',
  wav: 'file-music',
  flac: 'file-music',
  ogg: 'file-music',
  m4a: 'file-music',
  aac: 'file-music',
}
const ICON_BY_KIND: Partial<Record<ViewerKind, string>> = { code: 'file-code', html: 'file-code', image: 'file-image' }

export function iconForFile(name: string | null | undefined): string {
  const base = (name || '').toLowerCase()
  if (ICON_BY_NAME[base]) return ICON_BY_NAME[base]
  const ext = base.split('.').pop() ?? ''
  return ICON_BY_EXT[ext] || ICON_BY_KIND[viewerKind(name)] || 'file'
}

// The header tooltip/count for the "Mentioned in" backlink (ADR 0014). The label is
// always "Mentioned in N thread(s)" — the loose scan promises no more than that (not
// "referenced"). Zero is a valid input (the caller hides the affordance entirely).
export function mentionsLabel(count: number | null | undefined): string {
  return m.viewer_mentions({ count: count || 0 })
}

// The two MentionRow fields the popover reads.
type MentionRowRef = { kind: string; title?: string }

// One popover row's display text. A `run` row leads with its parent Task name (its
// stored `title` already falls back to that server-side) — a Run is an execution of a
// Task, so the Task name is what identifies it; a `chat` row shows the chat's title.
// A blank title degrades to a kind-appropriate placeholder rather than an empty row.
export function mentionRowTitle(row: MentionRowRef | null | undefined): string {
  if (!row) return ''
  return (row.title || '').trim() || (row.kind === 'run' ? m.viewer_task_run() : m.thread_back_chat())
}

// The glyph name distinguishing a Chat row from a Task Run row in the popover.
export function mentionRowIcon(row: MentionRowRef | null | undefined): string {
  return row?.kind === 'run' ? 'zap' : 'message'
}

// The ancestor Directories of a Files-space path, shallowest first — the folders
// that must be expanded to Reveal the file's own row. 'a/b/c.md' → ['a', 'a/b'].
// A root-level file, or an empty/nullish path, has none. Never includes the path
// itself (only Directories gate a file's visibility).
export function ancestorDirs(path: string | null | undefined): string[] {
  const parts = (path || '').split('/')
  parts.pop()   // drop the filename; keep only the containing Directories
  const dirs: string[] = []
  let acc = ''
  for (const p of parts) {
    if (!p) continue
    acc = acc ? acc + '/' + p : p
    dirs.push(acc)
  }
  return dirs
}
