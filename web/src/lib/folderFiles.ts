// Pure logic addressing a file across the two spaces: a Files-space file by a
// workspace-relative path, a Folder file by an absolute path (the sole discriminator).
import { m } from '../paraglide/messages.js'

// What a resolved Grant mode unlocks on a Folder file.
export type FolderAffordances = { edit: boolean; rename: boolean; delete: boolean; move: boolean }

// Classify a path: true → a Folder file (absolute or `~`-relative), false → Files-space.
export function isFolderPath(path: string | null | undefined): boolean {
  const p = String(path ?? '')
  return p.startsWith('/') || p.startsWith('~')
}

// The mutation affordances a resolved Grant mode unlocks: `read_write` unlocks
// edit/rename/delete/move; any other mode unlocks none (preview + download only).
export function folderAffordances(mode: string | null | undefined): FolderAffordances {
  const rw = mode === 'read_write'
  return { edit: rw, rename: rw, delete: rw, move: rw }
}

// The short badge label for a resolved mode: `read_write`→"read+write", `read`→"read",
// anything else→"" (no badge). The mode itself is the persisted value; only the
// badge text localizes.
export function modeLabel(mode: string | null | undefined): string {
  if (mode === 'read_write') return m.files_mode_read_write()
  if (mode === 'read') return m.files_mode_read()
  return ''
}

// The Directories to expand to Reveal a Folder file at absolute `path`: its granted
// `root` (inclusive) down to the file's own parent, shallowest first; [] if not under root.
export function folderAncestorDirs(root: string | null | undefined, path: string | null | undefined): string[] {
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
export function rawQuery(
  path: string | null | undefined,
  { download = false, chatId = '' }: { download?: boolean; chatId?: string } = {},
): string {
  const parts = ['path=' + encodeURIComponent(String(path ?? ''))]
  if (download) parts.push('download=true')
  if (isFolderPath(path) && chatId) parts.push('chat_id=' + encodeURIComponent(chatId))
  return parts.join('&')
}
