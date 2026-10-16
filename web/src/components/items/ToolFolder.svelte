<script lang="ts">
  import { revealFolder } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import { resolveToolPath } from '../../lib/toolPaths.ts'

  let { path, count, onopen }: { path: string; count: number; onopen: () => void } = $props()
  let unavailable = $state(false)

  async function openFolder() {
    try {
      const resolved = await resolveToolPath(path, api, 'directory')
      if (resolved === null) { unavailable = true; return }
      revealFolder(resolved)
      onopen()
    } catch {
      unavailable = true
    }
  }
</script>

<p>
  <button type="button" class="folder-name" onclick={openFolder} title={path} aria-label="Open folder {path}">{path}</button>{#if count > 1}<span class="run-count"> ×{count}</span>{/if}
  {#if unavailable}<span class="unavailable">Folder unavailable</span>{/if}
</p>

<style>
  p { flex: 1; min-width: 0; margin: 0; font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
  .folder-name { max-width: 100%; border: none; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-align: left; text-decoration: underline; text-underline-offset: 2px; overflow-wrap: anywhere; }
  .folder-name:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: 2px; }
  .run-count { color: var(--muted); white-space: nowrap; }
  .unavailable { display: block; color: var(--muted); font-size: 11px; }
</style>
