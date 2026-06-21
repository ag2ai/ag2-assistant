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
