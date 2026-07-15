<script>
  // Settings → "Profiles" section (§5.4). Lists all unarchived profiles. For the
  // ACTIVE profile it offers inline edit (rename / palette) via updateProfile; the
  // workspace folder is derived (not user-chosen) and shown read-only. After any save
  // we re-fetch /api/profiles and re-apply the palette if it changed.
  // Archive (§4.9): a quiet action per row; archiving the active_default requires
  // choosing a replacement (pre-selected). Archiving the ACTIVE profile navigates
  // to /app/ so boot re-resolves.
  import { profiles, settingsPage } from '../store.js'
  import { api } from '../transport/api.js'
  import { getActiveProfileId, setActiveProfileId } from '../lib/profile.js'
  import { PALETTES, setPalette, getPalette } from '../design/palette.js'
  import Icon from './Icon.svelte'

  const paletteHex = (id) => (PALETTES.find((p) => p.id === id) || {}).hex
  const list = $derived($profiles.list || [])
  const activeId = $derived($profiles.activeId || getActiveProfileId())
  // active_default from the registry (mirrored on the store when we refetch).
  let activeDefault = $state(null)

  // Palettes taken by OTHER profiles — hidden from the active profile's swatches;
  // the profile keeps its own.
  const claimedByOthers = $derived(
    list.filter((p) => p.id !== activeId).map((p) => p.palette)
  )

  // Inline editor state for the active profile.
  let editing = $state(false)
  let eName = $state('')
  let ePalette = $state('')
  let busy = $state(false)
  let err = $state('')

  // Archive confirm state: {pid, name, isActive} + the chosen replacement default.
  let confirmArchive = $state(null)
  let replacement = $state('')

  function startEdit(p) {
    err = ''
    eName = p.name
    ePalette = p.palette
    editing = true
  }
  function cancelEdit() { editing = false; err = '' }

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
      $profiles = { list: newList, activeId }
    } catch {}
  }

  async function save(p) {
    if (busy) return
    busy = true; err = ''
    const body = {}
    if (eName.trim() && eName.trim() !== p.name) body.name = eName.trim()
    if (ePalette && ePalette !== p.palette) body.palette = ePalette
    if (!Object.keys(body).length) { editing = false; busy = false; return }
    try {
      await api.updateProfile(p.id, body)
      const paletteChanged = 'palette' in body
      await refetch()
      // Re-apply the palette if the ACTIVE profile's palette changed (§5.4).
      if (paletteChanged && p.id === activeId && ePalette !== getPalette()) setPalette(ePalette)
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
        // The active profile is gone — let boot re-resolve to a valid one (§5.4).
        setActiveProfileId(null)
        location.assign('/app/')
        return
      }
      confirmArchive = null
      await refetch()
    } catch (e) {
      err = (e && e.message) || 'Could not archive profile'
      busy = false
    }
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
      <span class="pdot" style="--dot:{paletteHex(p.palette)}"></span>
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
            {#each PALETTES.filter((x) => !claimedByOthers.includes(x.id)) as sw (sw.id)}
              <button
                class="pswatch" class:on={ePalette === sw.id}
                style="--dot:{sw.hex}" title={sw.label} aria-label={sw.label}
                onclick={() => (ePalette = sw.id)}
              >{#if ePalette === sw.id}<Icon name="check" size={12} />{/if}</button>
            {/each}
          </div>
        </div>
        {#if err}<p class="perr">{err}</p>{/if}
        <div class="peditactions">
          <button class="linkbtn" disabled={busy} onclick={cancelEdit}>Cancel</button>
          <button class="open" disabled={busy} onclick={() => save(p)}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
    {/if}
  {/each}

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
</div>

<style>
  .profiles { display: flex; flex-direction: column; gap: 2px; }
  .prow {
    display: flex; align-items: center; gap: 10px; padding: 8px 4px;
    border-radius: var(--radius-sm);
  }
  .prow.active { background: var(--surface-sunk); padding: 8px; }
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

  .peditactions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; }

  .pconfirm {
    display: flex; flex-direction: column; gap: 10px;
    padding: 12px; margin: 6px 0;
    background: var(--surface-sunk); border: 1px solid var(--line); border-radius: var(--radius-sm);
  }
  .pconfirmhead { display: flex; align-items: center; gap: 7px; font-size: var(--text-sm); font-weight: var(--fw-semibold); }

  .perr { font-size: var(--text-sm); color: var(--danger, #d8552f); margin: 0; }
  .linkbtn.quiet { color: var(--text-muted); }
  .linkbtn.quiet:hover { color: var(--danger, #d8552f); }
  .open.danger { border-color: var(--danger, #d8552f); color: var(--danger, #d8552f); }
  .open.danger:hover { background: color-mix(in srgb, var(--danger, #d8552f) 12%, transparent); border-color: var(--danger, #d8552f); color: var(--danger, #d8552f); }
</style>
