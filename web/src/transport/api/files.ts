// Workspace files: the profile's Files space, the Folder roots browsable in the
// open Thread, the @-picker corpus and the preview rail's backlink (app.py
// 2923-3240). The /files/raw routes bypass the JSON helpers — they carry bytes
// and headers, and a 404 there is a missing file, not a vanished profile.
import { api as P } from '../../lib/profile.js'
import { parseEtag } from '../../lib/fileEdit.js'
import { rawQuery } from '../../lib/folderFiles.js'
import { ApiError, del, get, post } from '../http.ts'
import { parse } from '../validate.ts'
import {
  FilesResponse,
  FolderRoots,
  Mentions,
  MkdirResult,
  Ok,
  SearchResults,
  UploadResult,
} from '../../schemas/index.ts'

export type FileWithEtag = { text: string; etag: string | null; mode: string }

export const filesApi = {
  // The whole Files space: {root, files, dirs} — dirs includes the empty
  // Directories the files-only list omits, because the tree needs them.
  files: () => get(P('/files'), FilesResponse),

  // The Thread-scoped Folder section of the tree (ADR 0013). chatId scopes
  // chat-only grants; empty = profile-level grants only.
  folderRoots: (chatId = '') =>
    get(P('/folders/roots' + (chatId ? '?chat_id=' + encodeURIComponent(chatId) : '')), FolderRoots),

  // One Directory level inside a granted Folder (lazy-expand). `mode` is THIS
  // level's resolved Grant mode, which the tree's write affordances derive from.
  folderList: (path: string, chatId = '') =>
    get(
      P(
        '/files?path=' +
          encodeURIComponent(path) +
          (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''),
      ),
      FilesResponse,
    ),

  // Corpus search for the composer's @-picker (ADR 0012), ranked filename-first
  // and bounded; a blank/no-match query yields an empty list.
  searchFiles: (q: string, chatId = '') =>
    get(
      P(
        '/files/search?q=' +
          encodeURIComponent(q) +
          (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''),
      ),
      SearchResults,
    ),

  // The preview rail's "Mentioned in N threads" backlink (ADR 0014), newest-first.
  fileMentions: (path: string, chatId = '') =>
    get(
      P(
        '/files/mentions?path=' +
          encodeURIComponent(path) +
          (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''),
      ),
      Mentions,
    ),

  // A Folder (absolute) path carries chatId so the server resolves the Grant for
  // THIS Thread; a Files-space (relative) path ignores it (rawQuery decides).
  fileUrl: (path: string, download = false, chatId = ''): string =>
    P('/files/raw?' + rawQuery(path, { download, chatId })),

  fileText: async (path: string, chatId = ''): Promise<string> => {
    const r = await fetch(P('/files/raw?' + rawQuery(path, { chatId })))
    if (!r.ok) throw new Error('file not found')
    return r.text()
  },

  // Like fileText but also returns the served file's ETag, unquoted to match the
  // bare etag saveFile hands back, plus the X-File-Mode the edit affordance gates on.
  fileTextWithEtag: async (path: string, chatId = ''): Promise<FileWithEtag> => {
    const r = await fetch(P('/files/raw?' + rawQuery(path, { chatId })))
    if (!r.ok) throw new Error('file not found')
    const text = await r.text()
    return { text, etag: parseEtag(r.headers.get('ETag')), mode: r.headers.get('X-File-Mode') || '' }
  },

  // In-place write (ADR 0011): PUT the UTF-8 body with If-Match, resolving to the
  // new etag. 409/404/400 stay distinct on the thrown ApiError.
  saveFile: async (
    path: string,
    text: string,
    etag: string | null,
    chatId = '',
  ): Promise<string | null> => {
    const r = await fetch(P('/files/raw?' + rawQuery(path, { chatId })), {
      method: 'PUT',
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        ...(etag ? { 'If-Match': `"${etag}"` } : {}),
      },
      body: text,
    })
    if (!r.ok) {
      let payload: unknown = null
      try {
        payload = await r.json()
      } catch {
        // A non-JSON error body leaves the generic message in place.
      }
      const message = (payload as { error?: string } | null)?.error
      throw new ApiError(message || `save failed (${r.status})`, r.status, payload)
    }
    const data = await r.json().catch(() => ({}))
    return (data as { etag?: string }).etag || parseEtag(r.headers.get('ETag'))
  },

  // Deletes a file OR a Directory (recursive) — same route, extended server-side.
  deleteFile: (path: string, chatId = '') =>
    del(P('/files/raw?' + rawQuery(path, { chatId })), Ok),

  // Upload OS files into a target Directory (empty = root). Multipart, so it
  // skips the JSON envelope; name clashes are auto-suffixed, never overwritten.
  uploadFiles: async (fileList: Iterable<File>, dir = '', chatId = '') => {
    const fd = new FormData()
    for (const f of fileList) fd.append('files', f, f.name)
    fd.append('dir', dir)
    if (chatId) fd.append('chat_id', chatId)
    const r = await fetch(P('/files/upload'), { method: 'POST', body: fd })
    if (!r.ok) {
      let message = 'upload failed (' + r.status + ')'
      try {
        const b = await r.json()
        if (b && b.error) message = b.error
      } catch {
        // A non-JSON error body leaves the generic message in place.
      }
      throw new Error(message)
    }
    return parse(UploadResult, await r.json(), 'POST /files/upload')
  },

  // New empty Directory (409 if it already exists → shown as an inline error).
  mkdir: (path: string, chatId = '') =>
    post(P('/files/mkdir'), { path, chat_id: chatId }, MkdirResult),

  // Move/rename a file or Directory. 409 if the destination exists; a Folder move
  // is confined to the source's readable root server-side.
  moveFile: (from: string, to: string, chatId = '') =>
    post(P('/files/move'), { from, to, chat_id: chatId }, Ok),
}
