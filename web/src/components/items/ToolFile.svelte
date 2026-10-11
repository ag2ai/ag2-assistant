<script lang="ts">
  import { openAsideFile } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import Icon from '../Icon.svelte'
  import type { ToolCard } from '../../schemas/events.ts'

  let { file, onopen }: { file: Extract<ToolCard, { kind: 'file' }>; onopen: () => void } = $props()
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

<div class="file-actions">
  {#if gone}
    <span>File unavailable</span>
  {:else}
    <button type="button" class="iconbtn" onclick={openFile} title="Open file" aria-label="Open file {file.name}"><Icon name="file-text" size={15} /></button>
    <button type="button" class="iconbtn" onclick={downloadFile} title="Download file" aria-label="Download file {file.name}"><Icon name="download" size={15} /></button>
  {/if}
</div>

<style>
  .file-actions { display: flex; flex: none; align-items: center; gap: 4px; }
  span { color: var(--muted); font-size: 11px; }
</style>
