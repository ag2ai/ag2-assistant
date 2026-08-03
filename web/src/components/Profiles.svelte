<script>
  // Settings → Profiles roster (§5.4). A row list of unarchived profiles — the profile
  // MANAGEMENT head of the Profiles zone. A row is the entry point to that profile's
  // editor: clicking it (or its "Edit" link) fires onSelect(p); the parent (ProfilesPage)
  // switches the active profile to it and opens the tabbed ProfileEditor. Name/colour
  // editing NO LONGER lives inline here — it moved to the editor's General tab; this level
  // is roster + lifecycle (create / archive / restore / delete).
  // Archive (§4.9): archiving the active_default requires choosing a replacement
  // (pre-selected); archiving the ACTIVE profile switches to the replacement in place.
  // Archived section (ADR 0003): a collapsed disclosure with Restore + type-to-confirm Delete.
  import { profiles } from '../store.ts'
  import { api } from '../transport/api/index.ts'
  import { switchProfile, closeThread } from '../controller.ts'
  import { getActiveProfileId } from '../lib/profile.ts'
  import Icon from './Icon.svelte'
  import ProfileForm from './ProfileForm.svelte'

  let { onSelect } = $props()

  const list = $derived($profiles.list || [])
  const activeId = $derived($profiles.activeId || getActiveProfileId())
  // active_default from the registry (mirrored on the store when we refetch).
  let activeDefault = $state(null)

  let busy = $state(false)
  let err = $state('')

  // Archive confirm state: {pid, name, isActive, isActiveDefault} + chosen replacement default.
  let confirmArchive = $state(null)
  let replacement = $state('')

  // Create a new profile inline, without leaving Settings (reuses ProfileForm). Preset
  // accents already taken are nudged out of the swatches. On success we refetch and the new
  // profile joins the live list; we deliberately DON'T navigate to it, so the user stays here.
  let creating = $state(false)
  const claimedAccents = $derived(list.map((p) => p.accent))
  async function doCreate({ name, accent }) {
    await api.createProfile(name, accent) // throws → ProfileForm shows inline error
    creating = false
    await refetch()
  }

  // Archived profiles (ADR 0003): the collapsed "Archived" section.
  let archived = $state([])
  let showArchived = $state(false)
  // Delete confirm: {pid, name}. Type-to-confirm — the button stays disabled until
  // `deleteText` matches the profile name exactly.
  let confirmDelete = $state(null)
  let deleteText = $state('')

  async function refetch() {
    try {
      const reg = await api.profiles()
      const newList = reg.profiles || []
      activeDefault = reg.active_default
      archived = reg.archived || []
      if (confirmDelete && !archived.some((a) => a.id === confirmDelete.pid)) {
        confirmDelete = null
        deleteText = ''
      }
      $profiles = { list: newList, activeId }
    } catch {}
  }

  function askArchive(p) {
    err = ''
    const isActiveDefault = p.id === activeDefault
    const others = list.filter((x) => x.id !== p.id)
    const preferred = others.find((x) => x.id === activeDefault) || others[0]
    replacement = preferred ? preferred.id : ''
    confirmArchive = { pid: p.id, name: p.name, isActive: p.id === activeId, isActiveDefault }
  }

  async function doArchive() {
    if (busy || !confirmArchive) return
    busy = true; err = ''
    const { pid, isActive } = confirmArchive
    try {
      if (isActive) closeThread()
      await api.archiveProfile(pid, replacement || undefined)
      if (isActive) {
        if (replacement) { switchProfile(replacement); await refetch(); confirmArchive = null }
        else location.assign('/app/' + location.hash)
        return
      }
      confirmArchive = null
      await refetch()
    } catch (e) {
      err = (e && e.message) || 'Could not archive profile'
    } finally {
      busy = false
    }
  }

  async function doRestore(p) {
    if (busy) return
    busy = true; err = ''
    try {
      await api.restoreProfile(p.id)
      await refetch()
    } catch (e) {
      err = (e && e.message) || 'Could not restore profile'
    }
    busy = false
  }

  // Scope the accent tokens to a profile's OWN colour so its archived row + confirm follow
  // that profile, not the globally active accent.
  function accentVars(hex) {
    const ring = `color-mix(in srgb, ${hex} 40%, transparent)`
    return `--accent:${hex};--accent-ring:${ring};--focus-ring:0 0 0 3px ${ring};`
  }

  function askDelete(p) {
    err = ''
    deleteText = ''
    confirmDelete = { pid: p.id, name: p.name }
  }

  async function doDelete() {
    if (busy || !confirmDelete || deleteText !== confirmDelete.name) return
    busy = true; err = ''
    try {
      await api.deleteProfile(confirmDelete.pid)
      confirmDelete = null
      deleteText = ''
      await refetch()
    } catch (e) {
      err = (e && e.message) || 'Could not delete profile'
    }
    busy = false
  }

  // Row click just toggles the ACTIVE profile in place (like the Drawer chips / ⌘1..9);
  // no-ops on the active one. Opening the configuration is a separate, explicit action.
  function switchTo(p) { switchProfile(p.id) }
  // The explicit "Configure" button — opens this profile's editor (the parent switches to
  // it first if it isn't active, since the config zone is scoped to the active profile).
  function activate(p) { onSelect?.(p) }

  // Prime activeDefault on mount (cheap; the list itself comes from the store).
  refetch()
</script>

<div class="profiles">
  {#if err && !confirmArchive}<p class="perr">{err}</p>{/if}

  {#each list as p (p.id)}
    {@const isActive = p.id === activeId}
    <div
      class="prow" class:active={isActive} class:clickable={!isActive}
      role="button" aria-disabled={isActive}
      tabindex={isActive ? -1 : 0}
      title={isActive ? undefined : `Switch to ${p.name}`}
      onclick={isActive ? undefined : () => switchTo(p)}
      onkeydown={isActive ? undefined : (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchTo(p) } }}
    >
      <span class="pdot" style="--dot:{p.accent}"></span>
      <div class="pmeta">
        <div class="pname">
          {p.name}
          {#if isActive}<span class="pbadge">active</span>{/if}
        </div>
        <div class="ppath" title={p.workspace || ''}>{p.workspace || '—'}</div>
      </div>
      <div class="pactions">
        <button class="linkbtn" onclick={(e) => { e.stopPropagation(); activate(p) }}>Configure…</button>
        {#if list.length > 1}
          <button class="linkbtn quiet" onclick={(e) => { e.stopPropagation(); askArchive(p) }}>Archive…</button>
        {/if}
      </div>
    </div>
  {/each}

  {#if creating}
    <div class="peditor">
      <ProfileForm
        claimed={claimedAccents}
        submitLabel="Create profile"
        busyLabel="Creating…"
        onSubmit={doCreate}
      />
      <button class="linkbtn profile-add-cancel" onclick={() => (creating = false)}>Cancel</button>
    </div>
  {:else}
    <button class="profile-add" onclick={() => { err = ''; creating = true }}>
      <Icon name="plus" size={14} /> Add profile
    </button>
  {/if}

  {#if confirmArchive}
    <div class="pconfirm">
      <div class="pconfirmhead"><Icon name="archive" size={15} /> Archive “{confirmArchive.name}”?</div>
      <p class="phint">The profile stops running and is hidden. Its folder stays on disk.</p>
      {#if confirmArchive.isActiveDefault}
        <div class="pfield">
          <label for="pf-repl">Make this the new default</label>
          <select id="pf-repl" bind:value={replacement}>
            {#each list.filter((x) => x.id !== confirmArchive.pid) as o (o.id)}
              <option value={o.id}>{o.name}</option>
            {/each}
          </select>
        </div>
      {/if}
      {#if err}<p class="perr">{err}</p>{/if}
      <div class="peditactions">
        <button class="linkbtn" disabled={busy} onclick={() => (confirmArchive = null)}>Cancel</button>
        <button class="open danger" disabled={busy} onclick={doArchive}>{busy ? 'Archiving…' : 'Archive'}</button>
      </div>
    </div>
  {/if}

  <!-- Archived section (ADR 0003): collapsed by default, shown only when non-empty. -->
  {#if archived.length > 0}
    <div class="parch">
      <button class="parchhead" onclick={() => (showArchived = !showArchived)} aria-expanded={showArchived}>
        <Icon name={showArchived ? 'chevron-down' : 'chevron-right'} size={14} />
        Archived ({archived.length})
      </button>

      {#if showArchived}
        {#each archived as p (p.id)}
          <div class="parchentry" style={accentVars(p.accent)}>
            <div class="prow arch">
              <span class="pdot" style="--dot:{p.accent}"></span>
              <div class="pmeta"><div class="pname">{p.name}</div></div>
              <div class="pactions">
                <button class="linkbtn" disabled={busy} onclick={() => doRestore(p)}>Restore</button>
                <button class="linkbtn quiet" disabled={busy} onclick={() => askDelete(p)}>Delete…</button>
              </div>
            </div>

            {#if confirmDelete && confirmDelete.pid === p.id}
              <div class="pconfirm">
                <div class="pconfirmhead"><Icon name="trash" size={15} /> Delete “{confirmDelete.name}” permanently?</div>
                <p class="phint">Erases this profile's folder — chats, tasks, memory, and files — from disk. This cannot be undone.</p>
                <div class="pfield">
                  <label for="pf-del">Type <b>{confirmDelete.name}</b> to confirm</label>
                  <input id="pf-del" bind:value={deleteText} placeholder={confirmDelete.name} autocomplete="off" autocapitalize="off" spellcheck="false" />
                </div>
                {#if err}<p class="perr">{err}</p>{/if}
                <div class="peditactions">
                  <button class="linkbtn" disabled={busy} onclick={() => { confirmDelete = null; deleteText = '' }}>Cancel</button>
                  <button class="open danger" disabled={busy || deleteText !== confirmDelete.name} onclick={doDelete}>{busy ? 'Deleting…' : 'Delete permanently'}</button>
                </div>
              </div>
            {/if}
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .profiles { display: flex; flex-direction: column; gap: 2px; }
  /* Selection/highlight pattern mirrors the composer ModelSwitcher menu (.modelsw-item):
     soft accent-tint fill for the active row, neutral --code fill on hover, --focus-ring
     for keyboard focus. */
  .prow {
    display: flex; align-items: center; gap: 10px; padding: 8px 4px;
    border-radius: var(--radius-sm);
  }
  .prow.active { background: color-mix(in srgb, var(--accent) 10%, transparent); padding: 8px; }
  .prow.clickable { cursor: pointer; }
  .prow.clickable:hover { background: var(--code); }
  .prow.clickable.active:hover { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .prow.clickable:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .pdot { width: 12px; height: 12px; flex: none; border-radius: var(--radius-pill); background: var(--dot, var(--accent)); }
  .pmeta { flex: 1; min-width: 0; }
  .pname { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); font-weight: var(--fw-semibold); }
  .pbadge {
    font-size: var(--text-xs); font-weight: var(--fw-semibold); text-transform: uppercase;
    letter-spacing: var(--tracking-eyebrow); color: var(--accent);
    background: var(--accent-soft); border-radius: var(--radius-pill); padding: 1px 7px;
  }
  .ppath { font-size: var(--text-xs); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pactions { display: flex; align-items: center; gap: 10px; flex: none; }

  .peditor {
    display: flex; flex-direction: column; gap: 12px;
    padding: 12px; margin: 0 0 6px;
    background: var(--surface-sunk); border: 1px solid var(--line); border-radius: var(--radius-sm);
  }
  .pfield { display: flex; flex-direction: column; gap: 5px; }
  .pfield label { font-size: var(--text-xs); font-weight: var(--fw-semibold); color: var(--text-muted); }
  .pfield input, .pfield select {
    border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px;
    background-color: var(--bg); color: var(--text); font: inherit; font-size: var(--text-sm);
  }
  .pfield select { padding-right: 30px; }  /* clear the chevron */
  .pfield input:focus, .pfield select:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .phint { font-size: var(--text-xs); color: var(--text-muted); line-height: var(--leading-snug); }

  .peditactions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; }

  /* "Add profile" affordance at the foot of the live list. */
  .profile-add {
    display: inline-flex; align-items: center; gap: 6px; align-self: flex-start;
    margin-top: 6px; padding: 8px 4px;
    background: none; border: none; cursor: pointer; font: inherit;
    font-size: var(--text-sm); font-weight: var(--fw-semibold); color: var(--text-muted);
  }
  .profile-add:hover { color: var(--accent); }
  .profile-add:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }
  .profile-add-cancel { align-self: flex-end; }

  .pconfirm {
    display: flex; flex-direction: column; gap: 10px;
    padding: 12px; margin: 6px 0;
    background: var(--surface-sunk); border: 1px solid var(--line); border-radius: var(--radius-sm);
  }
  .pconfirmhead { display: flex; align-items: center; gap: 7px; font-size: var(--text-sm); font-weight: var(--fw-semibold); }

  /* Archived section (ADR 0003) */
  .parch { display: flex; flex-direction: column; gap: 2px; margin-top: 8px; border-top: 1px solid var(--line); padding-top: 8px; }
  .parchhead {
    display: flex; align-items: center; gap: 6px; padding: 6px 4px;
    background: none; border: none; cursor: pointer; font: inherit;
    font-size: var(--text-xs); font-weight: var(--fw-semibold); text-transform: uppercase;
    letter-spacing: var(--tracking-eyebrow); color: var(--text-muted);
  }
  .parchhead:hover { color: var(--accent); }
  .parchhead:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }
  .parchentry { display: flex; flex-direction: column; gap: 2px; }
  .prow.arch { padding: 8px 4px; }
  .prow.arch .pdot { opacity: 0.55; }
  .prow.arch .pname { color: var(--text-muted); font-weight: var(--fw-medium); }

  .perr { font-size: var(--text-sm); color: var(--danger, var(--danger)); margin: 0; }
  .linkbtn.quiet { color: var(--text-muted); }
  .linkbtn.quiet:hover { color: var(--danger, var(--danger)); }
  .open.danger { border-color: var(--danger, var(--danger)); color: var(--danger, var(--danger)); }
  .open.danger:hover { background: color-mix(in srgb, var(--danger, var(--danger)) 12%, transparent); border-color: var(--danger, var(--danger)); color: var(--danger, var(--danger)); }
</style>
