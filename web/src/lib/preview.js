// File extension → Viewer render kind (mirror of the tool→card registry).
// html / image / pdf use the browser's native rendering against /api/files/raw —
// the server already sends the right Content-Type, so no libraries are needed.
// md / code / text render in-app; anything unknown is download-only.

const KIND = {
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
export function viewerKind(name) {
  const ext = (name || '').split('.').pop().toLowerCase()
  return KIND[ext] || 'download'
}

// Whether the Files browser / tool card should offer a "view" affordance.
export const previewable = (name) => viewerKind(name) !== 'download'

// The ancestor Directories of a Files-space path, shallowest first — the folders
// that must be expanded to Reveal the file's own row. 'a/b/c.md' → ['a', 'a/b'].
// A root-level file, or an empty/nullish path, has none. Never includes the path
// itself (only Directories gate a file's visibility).
export function ancestorDirs(path) {
  const parts = (path || '').split('/')
  parts.pop()   // drop the filename; keep only the containing Directories
  const dirs = []
  let acc = ''
  for (const p of parts) {
    if (!p) continue
    acc = acc ? acc + '/' + p : p
    dirs.push(acc)
  }
  return dirs
}
