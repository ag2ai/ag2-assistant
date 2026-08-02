<script>
  // Profiles → Folders section (ADR 0015): the folders THIS profile can reach. Only
  // folders the active profile is granted appear (a two-way Read / Read+write switch,
  // no "off" — Delete is how you remove access). "Add a folder" registers it install-
  // wide then grants this profile read (a 409 on an already-registered path grants the
  // returned Folder). Delete revokes this profile's grant and, when that leaves the
  // Folder with no usages anywhere, removes it from the registry. Shares the one Folder
  // snapshot (lib/folders.js) with the composer chip strip and chat folder modal.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { profiles } from '../../store.ts'
  import { getActiveProfileId } from '../../lib/profile.js'
  import { getSettings } from './context.svelte.js'
  import { foldersStore, loadFolders, applyFolders } from '../../lib/folders.js'
  import Icon from '../Icon.svelte'
  import WriteSwitch from '../WriteSwitch.svelte'
  import FolderPicker from '../FolderPicker.svelte'

  const ctx = getSettings()

  let busy = $state(false)
  let err = $state('')
  let adding = $state(false)

  const pid = $derived($profiles.activeId || getActiveProfileId())

  // This profile's profile-scoped grant on a Folder (chat/task grants live elsewhere).
  const grantOf = (f, p) => (f.grants || []).find((g) => g.profile === p && !g.chat_id && !g.task_id)
  // Only Folders this profile is granted — no "off" rows.
  const granted = $derived($foldersStore.folders.filter((f) => grantOf(f, pid)))

  onMount(() => { if (!$foldersStore.loaded) loadFolders() })

  async function run(fn) {
    err = ''; busy = true
    try { applyFolders(await fn()) } catch (e) { err = String(e.message || e) }
    busy = false
  }

  function setMode(f, mode) {
    if (grantOf(f, pid)?.mode === mode) return
    run(() => api.setGrant(f.id, pid, mode))
  }

  // Register + grant this profile read. On a 409 (path already registered) grant the
  // returned existing Folder instead; never downgrade a grant it already has.
  async function addFolder(path) {
    err = ''; busy = true
    try {
      let folder
      try {
        const r = await api.createFolder(path)
        applyFolders(r)
        folder = r.folder
      } catch (e) {
        if (e.status === 409 && e.body?.existing) folder = e.body.existing
        else throw e
      }
      if (!grantOf(folder, pid)) applyFolders(await api.setGrant(folder.id, pid, 'read'))
      adding = false
    } catch (e) {
      err = String(e.message || e)
    }
    busy = false
  }

  // Disable the Folder for this profile. The revoke endpoint GCs the Folder itself when
  // that leaves it with no usages anywhere (no other profile / chat / task grant).
  const removeFolder = (f) => run(() => api.revokeGrant(f.id, pid))
</script>

<p class="muted permhint">Folders this profile may reach outside its default workspace </p>
{#if err}<p class="muted permerr">{err}</p>{/if}

{#if !granted.length}
  <p class="muted permempty">This profile has no folders yet — add one to give it access.</p>
{/if}
{#each granted as f (f.id)}
  {@const g = grantOf(f, pid)}
  <div class="foldercard">
    <span class="permico"><Icon name="folder" size={14} /></span>
    <span class="foldmeta">
      <span class="foldname">{f.name}</span>
      <span class="permval" title={f.path}>{f.path}{#if !f.exists} · <span class="missing">path not found</span>{/if}</span>
    </span>
    <div class="accctl">
      <WriteSwitch mode={g?.mode} disabled={busy} onchange={(m) => setMode(f, m)} />
      <button class="iconbtn" title="Delete folder" aria-label="Delete folder" disabled={busy} onclick={() => removeFolder(f)}><Icon name="trash" size={14} /></button>
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
  .foldercard { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; margin-bottom: 8px; display: flex; align-items: center; gap: 10px; }
  .permico { flex: none; display: inline-flex; color: var(--text-muted); }
  .foldmeta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .foldname { font-size: 13px; font-weight: var(--fw-semibold); color: var(--text); }
  .permval { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .missing { color: var(--warning, #b8860b); }
  .accctl { flex: none; display: inline-flex; align-items: center; gap: 10px; }
  .permadd { align-self: flex-start; }
  .permempty { font-size: 13px; margin: 0 0 8px; }
  .permhint { font-size: 12px; margin: 2px 0 10px; }
  .permerr { color: var(--danger); font-size: 13px; margin: 0 0 8px; }
</style>
