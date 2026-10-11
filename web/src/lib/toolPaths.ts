import type { FilesListing, FsListing } from '../schemas/index.ts'

type PathSource = {
  files: () => Promise<FilesListing>
  listDirs: () => Promise<FsListing>
}

// Address a tool path in the workspace or the host's granted folders.
export async function resolveToolPath(path: string, source: PathSource, kind: 'file' | 'directory' = 'file'): Promise<string | null> {
  if (path.startsWith('~') && path !== '~' && !path.startsWith('~/')) return null
  const [listing, home] = await Promise.all([
    source.files(),
    path.startsWith('~') ? source.listDirs() : null,
  ])
  if (home && !home.ok) return null
  let resolved = home?.ok ? home.path.replace(/\/$/, '') + path.slice(1) : path
  resolved = resolved.replace(/\/+$/, '')
  const root = listing.root.replace(/\/+$/, '')
  if (resolved === root) return kind === 'directory' ? '' : null
  if (resolved.startsWith(root + '/')) resolved = resolved.slice(root.length + 1)
  if (resolved.startsWith('/')) return resolved
  resolved = resolved.replace(/^(\.\/)+/, '')
  if (kind === 'directory') return resolved === '.' ? '' : resolved
  return listing.files.find(file => file.path === resolved)?.path
    ?? listing.files.find(file => file.path.endsWith('/' + resolved))?.path
    ?? null
}
