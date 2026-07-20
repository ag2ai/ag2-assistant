<script>
  // Settings → Folders: the install-wide Folder registry (CONTEXT.md "Folders",
  // ADR 0006). A Folder is a name + a path, unique by path — the only way disk
  // outside the Root becomes reachable. Profiles hold Grants (read / read+write);
  // chat-scoped Grants are managed from the chat's kebab menu, not here.
  // Shares ONE Folder snapshot (lib/folders.js) with the ChatFolders modal and
  // the composer chip strip, so a Folder added/removed or a grant flipped here
  // reaches those surfaces live. Every mutator replaces the store wholesale from
  // the endpoint's full-snapshot response.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { getSettings } from './context.svelte.js'
  import { profiles } from '../../store.js'
  import { foldersStore, loadFolders, applyFolders } from '../../lib/folders.js'
  import Icon from '../Icon.svelte'
  import FolderPicker from '../FolderPicker.svelte'
  import AccessSwitch from '../AccessSwitch.svelte'

  const ctx = getSettings()

  let busy = $state(false)
  let err = $state('')
  let adding = $state(false)
  let renamingId = $state('')
  let renameText = $state('')

  const plist = $derived($profiles.list || [])
  const folders = $derived($foldersStore.folders)

  onMount(() => { if (!$foldersStore.loaded) loadFolders() })
  async function run(fn) {
    err = ''; busy = true
    try { applyFolders(await fn()) } catch (e) { err = String(e.message || e) }
    busy = false
  }

  const addFolder = (path) => run(() => api.createFolder(path).then((r) => { adding = false; return r }))
  const removeFolder = (f) => {
    if (confirm(`Delete "${f.name}"? Every profile and chat loses access instantly.`))
      run(() => api.deleteFolder(f.id))
  }
  function startRename(f) { renamingId = f.id; renameText = f.name }
  function commitRename(f) {
    const t = renameText.trim()
    renamingId = ''
    if (t && t !== f.name) run(() => api.updateFolder(f.id, { name: t }))
  }
  function focusSelect(node) { node.focus(); node.select() }

  // Profile-scope grant for profile pid on folder f (chat grants live elsewhere).
  const grantOf = (f, pid) => (f.grants || []).find((g) => g.profile === pid && !g.chat_id && !g.task_id)
  function setMode(f, pid, mode) {
    const cur = grantOf(f, pid)
    if (!mode) { if (cur) run(() => api.revokeGrant(f.id, pid)); return }
    if (cur?.mode === mode) return
    run(() => api.setGrant(f.id, pid, mode))
  }
</script>

<div class="setsec">Folders</div>
<p class="muted permhint">Folders the assistant may reach outside its own workspace. Grants are per profile — <b>read</b> or <b>read&nbsp;+&nbsp;write</b>; no grant means no access. Chat-scoped grants are managed from each chat's menu.</p>
{#if err}<p class="muted permerr">{err}</p>{/if}

{#if !folders.length}<p class="muted permempty">No folders registered yet.</p>{/if}
{#each folders as f (f.id)}
  <div class="foldercard">
    <div class="permrow">
      <span class="permico"><Icon name="folder" size={14} /></span>
      {#if renamingId === f.id}
        <input
          class="renameinput" type="text" bind:value={renameText} use:focusSelect
          onkeydown={(e) => { if (e.key === 'Enter') commitRename(f); if (e.key === 'Escape') renamingId = '' }}
          onblur={() => commitRename(f)}
        />
      {:else}
        <button class="foldname" title="Rename" onclick={() => startRename(f)}>{f.name} <Icon name="pencil" size={11} /></button>
      {/if}
      <span class="permval" title={f.path}>{f.path}</span>
      {#if !f.exists}<span class="missing">path not found</span>{/if}
      <button class="linkbtn danger" disabled={busy} onclick={() => removeFolder(f)}>Delete</button>
    </div>
    <div class="grants">
      {#each plist as p (p.id)}
        {@const g = grantOf(f, p.id)}
        <div class="grantrow">
          <span class="gname">{p.name}</span>
          <AccessSwitch mode={g?.mode} disabled={busy} onchange={(m) => setMode(f, p.id, m)} />
        </div>
      {/each}
    </div>
  </div>
{/each}

{#if !adding}
  <button class="open permadd" onclick={() => (adding = true)}>Add a folder…</button>
{:else}
  <FolderPicker roots={ctx.s.fs || {}} start={ctx.s.fs?.cwd || ''} {busy} onUse={addFolder} />
  <div class="keyrow" style="justify-content:flex-end">
    <button class="linkbtn" onclick={() => (adding = false)}>Cancel</button>
  </div>
{/if}

<style>
  .foldercard { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; margin-bottom: 8px; display: flex; flex-direction: column; gap: 6px; }
  .permrow { display: flex; align-items: center; gap: 8px; }
  .permico { flex: none; display: inline-flex; color: var(--text-muted); }
  .permval { flex: 1; min-width: 0; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .foldname { display: inline-flex; align-items: center; gap: 4px; background: none; border: none; padding: 0; font-size: 13px; font-weight: var(--fw-semibold); color: var(--text); cursor: pointer; }
  .renameinput { font-size: 13px; }
  .missing { flex: none; font-size: 11px; color: var(--warning, #b8860b); border: 1px solid currentColor; border-radius: var(--radius-pill, 999px); padding: 1px 7px; }
  .grants { display: flex; flex-direction: column; gap: 4px; }
  .grantrow { display: flex; align-items: center; gap: 10px; }
  .gname { flex: none; min-width: 90px; font-size: 12px; color: var(--text); }
  .permadd { align-self: flex-start; }
  .permempty { font-size: 13px; margin: 0; }
  .permhint { font-size: 12px; margin: 2px 0 8px; }
  .permerr { color: #d8552f; font-size: 13px; margin: 0; }
</style>
