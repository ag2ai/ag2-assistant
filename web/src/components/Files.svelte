<script>
  // The agent's working file space — browse / view / download what it saved
  // (task deliverables land here as real files; chat "save as a file" too).
  import { onMount } from 'svelte'
  import { filesOpen, viewer } from '../store.js'
  import { api } from '../transport/api.js'

  let files = $state([])
  let root = $state('')
  let loading = $state(true)
  let err = $state('')

  async function load() {
    loading = true
    try {
      const r = await api.files()
      files = r.files || []
      root = r.root || ''
    } catch (e) {
      err = String(e.message || e)
    }
    loading = false
  }
  onMount(load)
  const close = () => ($filesOpen = false)

  const fmtSize = (n) =>
    n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`
  const fmtWhen = (iso) => {
    try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) }
    catch { return '' }
  }
  const viewable = (name) => /\.(md|markdown|txt|json|csv|ya?ml|py|js|ts|html|css|log)$/i.test(name)

  const groups = $derived.by(() => {
    const m = new Map()
    for (const f of files) {
      const k = f.dir || ''
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(f)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  })

  async function view(f) {
    try { $viewer = { title: f.name, text: await api.fileText(f.path) } }
    catch (e) { err = String(e.message || e) }
  }

  let confirming = $state('')   // path awaiting delete confirmation
  let busy = $state('')         // path currently being deleted
  async function del(f) {
    busy = f.path
    try {
      await api.deleteFile(f.path)
      files = files.filter((x) => x.path !== f.path)
    } catch (e) {
      err = String(e.message || e)
    }
    busy = ''
    confirming = ''
  }
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal files">
  <h2>Files</h2>
  <p class="muted">The agent's working file space — files it saved (task deliverables land here). <code>{root}</code></p>
  {#if err}<p class="muted" style="color:#d8552f">{err}</p>{/if}
  <div class="filescroll">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if !files.length}
      <p class="muted">No files yet — ask the agent to save something, or run a task that produces a deliverable.</p>
    {:else}
      {#each groups as [dir, list] (dir)}
        <div class="setsec">📁 {dir || '/'}</div>
        {#each list as f (f.path)}
          <div class="filerow">
            <span class="fname" title={f.path}>{f.name}</span>
            <span class="fmeta">{fmtSize(f.size)} · {fmtWhen(f.modified)}</span>
            {#if confirming === f.path}
              <span class="confirm">Delete?</span>
              <button class="linkbtn danger" disabled={busy === f.path} onclick={() => del(f)}>
                {busy === f.path ? '…' : 'yes'}
              </button>
              <button class="linkbtn" onclick={() => (confirming = '')}>no</button>
            {:else}
              {#if viewable(f.name)}<button class="linkbtn" onclick={() => view(f)}>view</button>{/if}
              <a class="linkbtn" href={api.fileUrl(f.path, true)}>download</a>
              <button class="linkbtn danger" onclick={() => (confirming = f.path)}>delete</button>
            {/if}
          </div>
        {/each}
      {/each}
    {/if}
  </div>
  <button class="modal-close" onclick={close}>Close</button>
</div>
