<script>
  import { viewer } from '../../store.js'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'

  let { item } = $props()
  let gone = $state(new Set()) // card ids whose file is no longer available

  function markGone(c) {
    const n = new Set(gone)
    n.add(c.id)
    gone = n
  }

  // A card path is relative to the tool's sandbox root: workspace-relative for chat
  // writes (directly addressable) or task-relative for subagent writes. If a direct
  // hit fails, consult the live file list — a suffix match means it still exists
  // under a task folder; no match means it was deleted from the Files browser.
  async function livePath(c) {
    try {
      const { files } = await api.files()
      const hit = files.find((f) => f.path === c.path) || files.find((f) => f.path.endsWith('/' + c.path))
      return hit ? hit.path : null
    } catch {
      return null
    }
  }

  async function openFile(c) {
    // Resolve the live workspace path (also catches deletion + task-relative paths);
    // the Viewer then renders by file type (html/image/pdf/markdown/code/…).
    const p = await livePath(c)
    if (p) $viewer = { title: c.name, name: c.name, path: p }
    else markGone(c)
  }

  async function downloadFile(c) {
    let path = c.path
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
            <button class="tcdl" onclick={() => downloadFile(c)} title="Download {c.name}" aria-label="Download {c.name}"><Icon name="download" size={15} /></button>
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
