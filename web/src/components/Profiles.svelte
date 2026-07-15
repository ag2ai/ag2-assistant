<script>
  // Settings → "Profiles" section (§5.4). Lists all unarchived profiles. For the
  // ACTIVE profile it offers inline edit (rename / palette) via updateProfile; the
  // workspace folder is derived (not user-chosen) and shown read-only. After any save
  // we re-fetch /api/profiles and re-apply the palette if it changed.
  // Archive (§4.9): a quiet action per row; archiving the active_default requires
  // choosing a replacement (pre-selected). Archiving the ACTIVE profile navigates
  // to /app/ so boot re-resolves.
  // Archived section (ADR 0003): a collapsed "Archived" disclosure (shown only when
  // non-empty) lists archived profiles with Restore (unarchive + boot) and a
  // type-to-confirm permanent Delete.
  import { onDestroy } from 'svelte'
  import { profiles, settingsPage } from '../store.js'
  import { api } from '../transport/api.js'
  import { getActiveProfileId, setActiveProfileId } from '../lib/profile.js'
  import { PALETTES, setAccent, getAccent } from '../design/palette.js'
  import Icon from './Icon.svelte'
  import ProfileForm from './ProfileForm.svelte'

  const list = $derived($profiles.list || [])
  const activeId = $derived($profiles.activeId || getActiveProfileId())
  // active_default from the registry (mirrored on the store when we refetch).
  let activeDefault = $state(null)

  // Preset accents taken by OTHER profiles — hidden from the active profile's
  // preset swatches (a nudge; a custom colour can still reproduce any of them).
  const claimedByOthers = $derived(
    list.filter((p) => p.id !== activeId).map((p) => p.accent)
  )

  // Inline editor state for the active profile.
  let editing = $state(false)
  let eName = $state('')
  let eAccent = $state('')
  // Accent applied when editing began — restored on rollback (§5.4). Picking a
  // swatch applies the scheme live (preview) without persisting to the backend;
  // if the edit ends without a save we revert to this.
  let origAccent = $state('')
  let busy = $state(false)
  let err = $state('')

  // The edited accent is a custom colour when it's not one of the presets.
  const eCustom = $derived(!PALETTES.some((x) => x.hex === eAccent))

  // Archive confirm state: {pid, name, isActive} + the chosen replacement default.
  let confirmArchive = $state(null)
  let replacement = $state('')

  // Create a new profile inline, without leaving Settings (reuses ProfileForm — the
  // same form as onboarding / the Drawer "+"). Preset accents already taken are nudged
  // out of the swatches. On success we refetch and the new profile joins the live list;
  // we deliberately DON'T navigate to it (unlike the Drawer), so the user stays here.
  let creating = $state(false)
  const claimedAccents = $derived(list.map((p) => p.accent))
  async function doCreate({ name, accent }) {
    await api.createProfile(name, accent) // throws → ProfileForm shows inline error
    creating = false
    await refetch()
  }

  // Archived profiles (ADR 0003): the collapsed "Archived" section. `archived` is
  // filled from the same /api/profiles fetch; the disclosure is closed by default.
  let archived = $state([])
  let showArchived = $state(false)
  // Delete confirm: {pid, name}. Type-to-confirm — the permanent-delete button stays
  // disabled until `deleteText` matches the profile name exactly.
  let confirmDelete = $state(null)
  let deleteText = $state('')

  function startEdit(p) {
    err = ''
    eName = p.name
    eAccent = p.accent
    origAccent = p.accent   // the profile's saved colour — baseline for rollback & save-diff
    editing = true
  }

  // Tint the active profile in the store so every surface bound to its accent
  // (Drawer monogram chips, the row dot here) re-renders with `hex`. Optimistic —
  // rolled back if the edit isn't saved.
  function tintActive(hex) {
    $profiles = {
      ...$profiles,
      list: (($profiles.list) || []).map((p) => (p.id === activeId ? { ...p, accent: hex } : p)),
    }
  }

  // Live-preview an accent: the active profile is the only one editable here, so
  // apply the whole scheme immediately — global accent (setAccent) AND the
  // profile-tinted surfaces (tintActive). Nothing is persisted until Save.
  function pickAccent(hex) {
    eAccent = hex
    setAccent(hex)
    tintActive(hex)
  }
  // Native colour input → any custom hex, previewed the same way.
  function pickCustom(e) {
    const v = (e.target.value || '').toLowerCase()
    if (/^#[0-9a-f]{6}$/.test(v)) pickAccent(v)
  }

  // Restore the pre-edit scheme if the live preview drifted from it — both the
  // global accent and the store tint.
  function rollbackAccent() {
    if (!origAccent) return
    if (getAccent() !== origAccent) setAccent(origAccent)
    if (($profiles.list || []).some((p) => p.id === activeId && p.accent !== origAccent)) tintActive(origAccent)
  }

  function cancelEdit() { rollbackAccent(); editing = false; err = '' }

  // Closing Settings (unmount) mid-edit counts as "not saved" → revert the preview.
  onDestroy(() => { if (editing) rollbackAccent() })

  // Switch the active profile (§5.4): a full-page nav to /app/{pid}/, the same
  // mechanism as the Drawer chips and ⌘1..9 shortcuts. App.svelte's boot adopts
  // the URL pid, persists it, and applies its palette. No-op on the active one.
  // Switching reloads the SPA (closing Settings); stash a flag so boot re-opens
  // Settings on the SAME page — the user stays where they were.
  function switchTo(p) {
    if (p.id === activeId) return
    try { sessionStorage.setItem('ag2-reopen-settings', $settingsPage || 'profiles') } catch {}
    location.assign('/app/' + p.id + '/')
  }

  async function refetch() {
    try {
      const reg = await api.profiles()
      const newList = reg.profiles || []
      activeDefault = reg.active_default
      archived = reg.archived || []
      // Drop a stale delete-confirm if its target is no longer archived (restored,
      // purged, or changed elsewhere) — otherwise re-archiving reopens the form.
      if (confirmDelete && !archived.some((a) => a.id === confirmDelete.pid)) {
        confirmDelete = null
        deleteText = ''
      }
      $profiles = { list: newList, activeId }
    } catch {}
  }

  async function save(p) {
    if (busy) return
    busy = true; err = ''
    const body = {}
    if (eName.trim() && eName.trim() !== p.name) body.name = eName.trim()
    // Diff against origAccent, not p.accent — the store already holds the
    // optimistic preview value, so p.accent === eAccent here.
    if (eAccent && eAccent !== origAccent) body.accent = eAccent
    if (!Object.keys(body).length) { editing = false; busy = false; return }
    try {
      await api.updateProfile(p.id, body)
      const accentChanged = 'accent' in body
      await refetch()
      // The scheme was already applied live while picking; only re-assert it here
      // for the ACTIVE profile if something drifted it (§5.4).
      if (accentChanged && p.id === activeId && eAccent !== getAccent()) setAccent(eAccent)
      origAccent = eAccent   // the preview is now the committed baseline
      editing = false
    } catch (e) {
      err = (e && e.message) || 'Could not save profile'
    }
    busy = false
  }

  function askArchive(p) {
    err = ''
    const isActiveDefault = p.id === activeDefault
    // Pre-select a replacement default: the registry default if archiving it, else
    // the first other profile (§4.9 UI pre-selects).
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
      await api.archiveProfile(pid, replacement || undefined)
      if (isActive) {
        // The active profile is gone — the SPA must reload so boot re-resolves to a
        // valid one (§5.4). Stash the reopen flag (like switchTo) so Settings comes
        // back on the same page instead of closing under the user.
        try { sessionStorage.setItem('ag2-reopen-settings', $settingsPage || 'profiles') } catch {}
        setActiveProfileId(null)
        location.assign('/app/')
        return
      }
      confirmArchive = null
      await refetch()
    } catch (e) {
      err = (e && e.message) || 'Could not archive profile'
    } finally {
      // Always clear busy — the success (non-active) path forgot to, leaving every
      // button (incl. the archived rows' Restore/Delete) stuck disabled until remount.
      busy = false
    }
  }

  // Restore an archived profile (unarchive + boot live, ADR 0003). Re-fetch so it
  // moves from the Archived section back into the live list.
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

  // Scope the accent tokens to a profile's OWN colour so its archived row + confirm
  // (Restore hover, input border + focus ring) follow that profile, not the globally
  // active accent. `--accent-ring`/`--focus-ring` are frozen with the root accent at
  // :root, so overriding `--accent` alone wouldn't reach them — redeclare all three.
  function accentVars(hex) {
    const ring = `color-mix(in srgb, ${hex} 40%, transparent)`
    return `--accent:${hex};--accent-ring:${ring};--focus-ring:0 0 0 3px ${ring};`
  }

  function askDelete(p) {
    err = ''
    deleteText = ''
    confirmDelete = { pid: p.id, name: p.name }
  }

  // Permanent delete (ADR 0003) — guarded by the type-to-confirm match in the markup;
  // re-check here so a stray call can never purge on a mismatch.
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

  // Prime activeDefault on mount (cheap; the list itself comes from the store).
  refetch()
</script>

<div class="profiles">
  {#if err && !editing && !confirmArchive}<p class="perr">{err}</p>{/if}

  {#each list as p (p.id)}
    {@const isActive = p.id === activeId}
    <div
      class="prow" class:active={isActive} class:clickable={!isActive}
      role={isActive ? undefined : 'button'}
      tabindex={isActive ? undefined : 0}
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
        {#if isActive && !editing}
          <button class="linkbtn" onclick={() => startEdit(p)}>Edit</button>
        {/if}
        {#if list.length > 1}
          <button class="linkbtn quiet" onclick={(e) => { e.stopPropagation(); askArchive(p) }}>Archive…</button>
        {/if}
      </div>
    </div>

    {#if isActive && editing}
      <div class="peditor">
        <div class="pfield">
          <label for="pf-name">Name</label>
          <input id="pf-name" bind:value={eName} placeholder="Profile name" />
        </div>
        <div class="pfield">
          <span class="plabel">Colour</span>
          <div class="pdots">
            {#each PALETTES.filter((x) => !claimedByOthers.includes(x.hex) || x.hex === origAccent) as sw (sw.id)}
              <button
                class="pswatch" class:on={eAccent === sw.hex}
                style="--dot:{sw.hex}" title={sw.label} aria-label={sw.label}
                onclick={() => pickAccent(sw.hex)}
              >{#if eAccent === sw.hex}<Icon name="check" size={12} />{/if}</button>
            {/each}
            <!-- Custom colour: native <input type=color> behind the swatch. Always
                 shows the palette glyph (never the current colour) — its job is to
                 open the picker; the selected hex reads below in .phex. -->
            <label
              class="pswatch pcustom rainbow" class:on={eCustom} title="Custom colour"
            >
              <Icon name="palette" size={14} />
              <input type="color" value={eAccent} oninput={pickCustom} aria-label="Custom colour" />
            </label>
          </div>
          <div class="phex">{eAccent}{#if eCustom} · custom{/if}</div>
        </div>
        {#if err}<p class="perr">{err}</p>{/if}
        <div class="peditactions">
          <button class="linkbtn" disabled={busy} onclick={cancelEdit}>Cancel</button>
          <button class="open" disabled={busy} onclick={() => save(p)}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    {/if}
  {/each}

  {#if creating}
    <div class="peditor">
      <ProfileForm
        claimed={claimedAccents}
        submitLabel="Create profile"
        busyLabel="Creating…"
        onSubmit={doCreate}
      />
      <button class="linkbtn padd-cancel" onclick={() => (creating = false)}>Cancel</button>
    </div>
  {:else}
    <button class="padd" onclick={() => { err = ''; creating = true }}>
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
  .prow {
    display: flex; align-items: center; gap: 10px; padding: 8px 4px;
    border: 1px solid transparent; border-radius: var(--radius-sm);
  }
  .prow.active { background: var(--surface-sunk); border-color: var(--accent); padding: 7px; }
  .prow.clickable { cursor: pointer; }
  .prow.clickable:hover { background: var(--surface-sunk); }
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
  .pfield label, .plabel { font-size: var(--text-xs); font-weight: var(--fw-semibold); color: var(--text-muted); }
  .pfield input, .pfield select {
    border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px;
    background: var(--bg); color: var(--text); font: inherit; font-size: var(--text-sm);
  }
  .pfield input:focus, .pfield select:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .phint { font-size: var(--text-xs); color: var(--text-muted); line-height: var(--leading-snug); }

  .pdots { display: flex; flex-wrap: wrap; gap: 8px; }
  .pswatch {
    width: 26px; height: 26px; flex: none; cursor: pointer; color: #fff;
    border-radius: var(--radius-pill); border: 2px solid transparent; background: var(--dot);
    display: inline-flex; align-items: center; justify-content: center;
    transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .pswatch:hover { transform: scale(1.08); }
  .pswatch.on { box-shadow: 0 0 0 2px var(--surface-sunk), 0 0 0 4px var(--dot); }

  .pcustom { position: relative; overflow: hidden; padding: 0; }
  .pcustom.rainbow {
    background: conic-gradient(from 90deg, #f95339, #ec5d18, #e0b400, #2f8c44, #109e91, #2f6fe0, #7a52ec, #f95339);
  }
  /* The palette glyph sits over the gradient — a drop-shadow keeps it legible. */
  .pcustom :global(svg) { filter: drop-shadow(0 0 1.5px rgba(0, 0, 0, 0.55)); }
  .pcustom.rainbow.on { box-shadow: 0 0 0 2px var(--surface-sunk), 0 0 0 4px var(--accent); }
  .pcustom input {
    position: absolute; inset: 0; width: 100%; height: 100%;
    opacity: 0; cursor: pointer; border: none; padding: 0; background: none;
  }

  .phex {
    font-size: var(--text-xs); color: var(--text-muted);
    font-variant-numeric: tabular-nums; letter-spacing: .02em;
  }

  .peditactions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; }

  /* "Add profile" affordance at the foot of the live list. */
  .padd {
    display: inline-flex; align-items: center; gap: 6px; align-self: flex-start;
    margin-top: 6px; padding: 8px 4px;
    background: none; border: none; cursor: pointer; font: inherit;
    font-size: var(--text-sm); font-weight: var(--fw-semibold); color: var(--text-muted);
  }
  .padd:hover { color: var(--text); }
  .padd:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }
  .padd-cancel { align-self: flex-end; }

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
  .parchhead:hover { color: var(--text); }
  .parchhead:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }
  .parchentry { display: flex; flex-direction: column; gap: 2px; }
  .prow.arch { padding: 8px 4px; }
  .prow.arch .pdot { opacity: 0.55; }
  .prow.arch .pname { color: var(--text-muted); font-weight: var(--fw-medium); }

  .perr { font-size: var(--text-sm); color: var(--danger, #d8552f); margin: 0; }
  .linkbtn.quiet { color: var(--text-muted); }
  .linkbtn.quiet:hover { color: var(--danger, #d8552f); }
  .open.danger { border-color: var(--danger, #d8552f); color: var(--danger, #d8552f); }
  .open.danger:hover { background: color-mix(in srgb, var(--danger, #d8552f) 12%, transparent); border-color: var(--danger, #d8552f); color: var(--danger, #d8552f); }
</style>
