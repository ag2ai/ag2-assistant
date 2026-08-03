<script lang="ts">
  import { openAsideFile } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import Icon from '../Icon.svelte'
  import type { ThreadItem, ToolCard } from '../../schemas/events.ts'

  // Only file cards carry a path — the handlers below run behind that branch.
  type FileCard = Extract<ToolCard, { kind: 'file' }>

  type Props = { item: Extract<ThreadItem, { kind: 'tools' }> }
  let { item }: Props = $props()
  let gone = $state(new Set<number>()) // card ids whose file is no longer available

  function markGone(c: FileCard) {
    const n = new Set(gone)
    n.add(c.id)
    gone = n
  }

  // A card path is relative to the tool's sandbox root: workspace-relative for chat
  // writes (directly addressable) or task-relative for subagent writes. If a direct
  // hit fails, consult the live file list — a suffix match means it still exists
  // under a task folder; no match means it was deleted from the Files browser.
  async function livePath(c: FileCard) {
    try {
      const { files } = await api.files()
      const hit = files.find((f) => f.path === c.path) || files.find((f) => f.path.endsWith('/' + c.path))
      return hit ? hit.path : null
    } catch {
      return null
    }
  }

  async function openFile(c: FileCard) {
    // Resolve the live workspace path (also catches deletion + task-relative paths);
    // the Viewer then renders by file type (html/image/pdf/markdown/code/…).
    const p = await livePath(c)
    if (p) openAsideFile(p)
    else markGone(c)
  }

  async function downloadFile(c: FileCard) {
    let path: string | null = c.path
    let r = await fetch(api.fileUrl(path, true)).catch(() => null)
    if (!r || !r.ok) {
      path = await livePath(c)
      r = path ? await fetch(api.fileUrl(path, true)).catch(() => null) : null
      if (!r || !r.ok) {
        markGone(c)
        return
      }
    }
    const url = URL.createObjectURL(await r.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = c.name
    a.click()
    URL.revokeObjectURL(url)
  }
</script>

<div class="tools">
  {#each item.names as t}
    <span class="chip"><Icon name="zap" size={12} /> {t.name}{t.n > 1 ? ' ×' + t.n : ''}</span>
  {/each}
</div>
{#if item.cards?.length}
  <div class="toolcards">
    {#each item.cards as c (c.id)}
      {#if c.kind === 'file'}
        <div class="toolcard" class:gone={gone.has(c.id)}>
          <span class="tcicon"><Icon name="file-text" size={15} /></span>
          {#if gone.has(c.id)}
            <span class="tcname">{c.name}</span>
            <span class="tcgone">deleted</span>
          {:else}
            <button class="tclink" onclick={() => openFile(c)} title={c.path}>{c.name}</button>
            <button class="tcdl" onclick={() => downloadFile(c)} title="Download file" aria-label="Download file"><Icon name="download" size={15} /></button>
          {/if}
        </div>
      {:else if c.kind === 'search'}
        <div class="toolcard">
          <span class="tcicon"><Icon name="search" size={15} /></span>
          <span class="tcquery" title="Search query">{c.query}</span>
        </div>
      {:else if c.kind === 'image'}
        <div class="toolcard" title={c.edit ? 'Editing image' : 'Generating image'}>
          <span class="tcicon"><Icon name="image" size={15} /></span>
          <span class="tcquery">{c.edit ? 'Edit' : 'Image'} · {c.prompt}</span>
        </div>
      {:else if c.kind === 'skill'}
        <div class="toolcard" title={c.ran ? `Ran script ${c.script} from skill ${c.name}` : `Used skill ${c.name}`}>
          <span class="tcicon"><Icon name="code" size={15} /></span>
          <span class="tcskill">Skill · {c.name}{c.ran ? ` · ▶ ${c.script}` : ''}</span>
        </div>
      {/if}
    {/each}
  </div>
{/if}
