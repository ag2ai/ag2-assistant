// Client-side mirror of `invalid_dir_name` in src/assistant/workspace.py, so the folder
// picker can reject a bad name as it's typed instead of after a round trip. The server
// stays authoritative — this only buys instant feedback, and the two rule sets are kept
// in step by folderName.test.mjs and its Python counterpart in tests/test_workspace.py.
// Import-free so it unit-tests under node:test.

// Longest single filename component on the filesystems we target (POSIX NAME_MAX).
const NAME_MAX = 255

// Byte length under UTF-8 — a name can pass a character count and still bust NAME_MAX
// once non-ASCII (accents, CJK, emoji) is encoded, which is what the kernel measures.
const byteLength = (s) => new TextEncoder().encode(s).length

// Why `name` is unusable as a NEW single folder name, or null if it's fine. Messages are
// written for the person typing and match the server's wording exactly, so a locally
// caught problem and a server-rejected one read identically.
export function invalidFolderName(name) {
  if (!name || !name.trim()) return 'Enter a folder name'
  if (name !== name.trim()) return "Name can't start or end with a space"
  if (name.includes('/') || name.includes('\\')) return "Name can't contain slashes"
  if (name === '.' || name === '..') return 'Not a valid folder name'
  // It would be created and then immediately hidden — the picker skips dotfolders.
  if (name.startsWith('.')) return "Names starting with a dot are hidden and won't show here"
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(name)) return 'Name contains invalid characters'
  if (byteLength(name) > NAME_MAX) return 'Name is too long'
  return null
}
