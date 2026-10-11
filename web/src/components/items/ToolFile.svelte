<script lang="ts">
  import { openAsideFile } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import Icon from '../Icon.svelte'
  import type { ToolCard } from '../../schemas/events.ts'

  let { file, count, onopen }: { file: Extract<ToolCard, { kind: 'file' }>; count: number; onopen: () => void } = $props()
  let gone = $state(false)

  // Resolve workspace-relative files and task-relative paths against the live list.
  async function livePath() {
    try {
      const { files } = await api.files()
      const hit = files.find(f => f.path === file.path) || files.find(f => f.path.endsWith('/' + file.path))
      return hit ? hit.path : null
    } catch {
      return null
    }
  }

  async function openFile() {
    const path = await livePath()
    if (path) { openAsideFile(path); onopen() }
    else gone = true
  }

  async function downloadFile() {
    let path: string | null = file.path
    let response = await fetch(api.fileUrl(path, true)).catch(() => null)
    if (!response || !response.ok) {
      path = await livePath()
      response = path ? await fetch(api.fileUrl(path, true)).catch(() => null) : null
      if (!response || !response.ok) { gone = true; return }
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    link.click()
    URL.revokeObjectURL(url)
  }
</script>

<div class="file-entry">
  <div class="file-label">
    {#if gone}
      <span class="file-gone">{file.path}</span>
    {:else}
      <button type="button" class="file-name" onclick={openFile} title={file.path} aria-label="Open file {file.name}">{file.path}</button>
    {/if}
    {#if count > 1}<span class="run-count"> ×{count}</span>{/if}
    {#if gone}<span class="file-unavailable">File unavailable</span>{/if}
  </div>
  {#if !gone}
    <button type="button" class="iconbtn" onclick={downloadFile} title="Download file" aria-label="Download file {file.name}"><Icon name="download" size={15} /></button>
  {/if}
</div>

<style>
  .file-entry { display: flex; flex: 1; min-width: 0; align-items: center; gap: 8px; }
  .file-label { flex: 1; min-width: 0; font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
  .file-name { max-width: 100%; border: none; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-align: left; text-decoration: underline; text-underline-offset: 2px; overflow-wrap: anywhere; }
  .file-name:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: 2px; }
  .run-count { color: var(--muted); white-space: nowrap; }
  .file-gone { color: var(--muted); text-decoration: line-through; }
  .file-unavailable { display: block; color: var(--muted); font-size: 11px; }
</style>
