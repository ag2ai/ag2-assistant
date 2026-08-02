// Files: the profile's Files space, granted Folder directories, the @-picker
// corpus, and the preview rail's backlink.
import { z } from 'zod'
import { Mode } from './primitives.ts'

// workspace.py list_files — one row per file, workspace-relative.
export const FileRow = z.object({
  path: z.string(),
  name: z.string(),
  dir: z.string(),
  size: z.number(),
  modified: z.string(),
})
export type FileRow = z.infer<typeof FileRow>

// GET /files with no (or a relative) path — the whole Files space. `dirs` is a
// flat list of workspace-relative directory paths (workspace.py list_all_dirs).
export const FilesListing = z.object({
  root: z.string(),
  files: z.array(FileRow),
  dirs: z.array(z.string()),
})
export type FilesListing = z.infer<typeof FilesListing>

// GET /files with an absolute path — one Directory level inside a granted Folder.
export const FolderListing = z.object({
  path: z.string(),
  dirs: z.array(z.object({ name: z.string(), path: z.string() })),
  files: z.array(z.object({ name: z.string(), path: z.string(), size: z.number() })),
  mode: Mode,
})
export type FolderListing = z.infer<typeof FolderListing>

// One route, two shapes — the branch is chosen by whether the path is absolute.
export const FilesResponse = z.union([FilesListing, FolderListing])
export type FilesResponse = z.infer<typeof FilesResponse>

// filesearch.py _candidate — kind is only ever file or directory.
export const SearchHit = z.object({
  path: z.string(),
  name: z.string(),
  dir: z.string(),
  kind: z.enum(['file', 'directory']),
})
export type SearchHit = z.infer<typeof SearchHit>

export const SearchResults = z.object({ results: z.array(SearchHit) })
export type SearchResults = z.infer<typeof SearchResults>

const MentionChat = z.object({
  stream_id: z.string(),
  kind: z.literal('chat'),
  title: z.string(),
  updated: z.string(),
})

const MentionRun = z.object({
  stream_id: z.string(),
  kind: z.literal('run'),
  title: z.string(),
  updated: z.string(),
  task_id: z.string(),
  task_name: z.string(),
  run_started_at: z.string(),
})

export const MentionRow = z.discriminatedUnion('kind', [MentionChat, MentionRun])
export type MentionRow = z.infer<typeof MentionRow>

export const Mentions = z.object({ threads: z.array(MentionRow) })
export type Mentions = z.infer<typeof Mentions>

export const UploadResult = z.object({ ok: z.literal(true), saved: z.array(z.string()) })
export type UploadResult = z.infer<typeof UploadResult>

export const MkdirResult = z.object({ ok: z.literal(true), path: z.string() })
export type MkdirResult = z.infer<typeof MkdirResult>

// PUT /files/raw — the new content token an optimistic-concurrency write returns.
// It rides both the JSON body and the ETag header; this is the body form.
export const WriteResult = z.object({ ok: z.literal(true), etag: z.string() })
export type WriteResult = z.infer<typeof WriteResult>
