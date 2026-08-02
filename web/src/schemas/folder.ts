// Folders — directories outside the Root, granted per profile/task/chat.
import { z } from 'zod'
import { Mode } from './primitives.ts'

export const Grant = z.object({
  profile: z.string(),
  chat_id: z.string(),
  task_id: z.string(),
  mode: Mode,
})
export type Grant = z.infer<typeof Grant>

export const Folder = z.object({
  id: z.string(),
  name: z.string(),
  path: z.string(),
  exists: z.boolean(),
  grants: z.array(Grant),
})
export type Folder = z.infer<typeof Folder>

export const FolderList = z.object({ folders: z.array(Folder) })
export type FolderList = z.infer<typeof FolderList>

// Create/update return the changed folder plus the whole snapshot.
export const FolderSaved = z.object({
  ok: z.literal(true),
  folder: Folder,
  folders: z.array(Folder),
})
export type FolderSaved = z.infer<typeof FolderSaved>

// Delete and the grant routes echo the snapshot only.
export const FolderMutated = z.object({ ok: z.literal(true), folders: z.array(Folder) })
export type FolderMutated = z.infer<typeof FolderMutated>

// The Thread-scoped roots the Files tree browses (ADR 0013).
export const FolderRoot = z.object({
  id: z.string(),
  name: z.string(),
  path: z.string(),
  mode: Mode,
  exists: z.boolean(),
})
export type FolderRoot = z.infer<typeof FolderRoot>

export const FolderRoots = z.object({ roots: z.array(FolderRoot) })
export type FolderRoots = z.infer<typeof FolderRoots>
