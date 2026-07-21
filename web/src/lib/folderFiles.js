// Pure logic addressing a file across the two spaces: a Files-space file by a
// workspace-relative path, a Folder file by an absolute path (the sole discriminator).

// Classify a path: true → a Folder file (absolute or `~`-relative), false → Files-space.
export function isFolderPath(path) {
  const p = String(path ?? '')
  return p.startsWith('/') || p.startsWith('~')
}

// The mutation affordances a resolved Grant mode unlocks: `read_write` unlocks
// edit/rename/delete/move; any other mode unlocks none (preview + download only).
export function folderAffordances(mode) {
  const rw = mode === 'read_write'
  return { edit: rw, rename: rw, delete: rw, move: rw }
}

// The short badge label for a resolved mode: `read_write`→"read+write", `read`→"read",
// anything else→"" (no badge).
export function modeLabel(mode) {
  if (mode === 'read_write') return 'read+write'
  if (mode === 'read') return 'read'
  return ''
}

// The Directories to expand to Reveal a Folder file at absolute `path`: its granted
// `root` (inclusive) down to the file's own parent, shallowest first; [] if not under root.
export function folderAncestorDirs(root, path) {
  const r = String(root ?? '').replace(/\/+$/, '')
  const p = String(path ?? '')
  if (!r || !p.startsWith(r + '/')) return []
  const rest = p.slice(r.length + 1).split('/')
  rest.pop()   // drop the filename; only Directories gate a file's visibility
  const dirs = [r]
  let acc = r
  for (const seg of rest) {
    if (!seg) continue
    acc = acc + '/' + seg
    dirs.push(acc)
  }
  return dirs
}

// Build the `/files/raw` query for `path`. A Folder (absolute) path carries `chat_id`
// so the server resolves the Grant for this Thread; `download` flips inline→attachment.
export function rawQuery(path, { download = false, chatId = '' } = {}) {
  const parts = ['path=' + encodeURIComponent(String(path ?? ''))]
  if (download) parts.push('download=true')
  if (isFolderPath(path) && chatId) parts.push('chat_id=' + encodeURIComponent(chatId))
  return parts.join('&')
}
