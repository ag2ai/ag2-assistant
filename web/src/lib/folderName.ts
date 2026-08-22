// Client-side mirror of `invalid_dir_name` in src/assistant/workspace.py, so the folder
// picker can reject a bad name as it's typed instead of after a round trip. The RULES
// are shared (folderName.test.ts and tests/test_workspace.py pin the same table); the
// wording is not — it is UI chrome, so it reads in the UI language (ADR 0031).
import { m } from '../paraglide/messages.js'

// Longest single filename component on the filesystems we target (POSIX NAME_MAX).
const NAME_MAX = 255

// Byte length under UTF-8 — a name can pass a character count and still bust NAME_MAX
// once non-ASCII (accents, CJK, emoji) is encoded, which is what the kernel measures.
const byteLength = (s: string) => new TextEncoder().encode(s).length

// Why `name` is unusable as a NEW single folder name, or null if it's fine. Each reason
// resolves at call time, so it lands in the language active when the field is validated.
export function invalidFolderName(name: string | null | undefined): string | null {
  if (!name || !name.trim()) return m.folder_name_required()
  if (name !== name.trim()) return m.folder_name_edge_space()
  if (name.includes('/') || name.includes('\\')) return m.folder_name_slashes()
  if (name === '.' || name === '..') return m.folder_name_invalid()
  // It would be created and then immediately hidden — the picker skips dotfolders.
  if (name.startsWith('.')) return m.folder_name_dot_hidden()
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(name)) return m.folder_name_bad_chars()
  if (byteLength(name) > NAME_MAX) return m.folder_name_too_long()
  return null
}
