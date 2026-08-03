<script>
  // Profile editor → General tab (ADR 0015, redesign §3): the active profile's IDENTITY —
  // name, accent colour, and the derived workspace (read-only). This is the editing home
  // that used to live inline in the Profiles list; the list is now a catalogue of cards.
  // Only the ACTIVE profile is editable (the whole Settings zone is scoped to it); picking
  // a swatch previews the scheme live (setAccent + store tint) and rolls back if unsaved.
  import { onDestroy } from 'svelte'
  import { profiles } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import { getActiveProfileId } from '../../lib/profile.ts'
  import { PALETTES, setAccent, getAccent } from '../../design/palette.js'
  import Icon from '../Icon.svelte'

  const list = $derived($profiles.list || [])
  const activeId = $derived($profiles.activeId || getActiveProfileId())
  const active = $derived(list.find((p) => p.id === activeId) || null)

  // Preset accents taken by OTHER profiles — nudged out of the swatches.
  const claimedByOthers = $derived(list.filter((p) => p.id !== activeId).map((p) => p.accent))

  let eName = $state('')
  let eAccent = $state('')
  // The saved colour when this profile was last synced — baseline for rollback + save-diff.
  let origAccent = $state('')
  let origName = $state('')
  let busy = $state(false)
  let saved = $state(false)
  let err = $state('')

  // Re-seed the draft whenever the active profile changes (switch) — but never clobber an
  // in-flight edit of the SAME profile. Keyed on the profile id.
  let seededId = $state(null)
  $effect(() => {
    if (active && active.id !== seededId) {
      seededId = active.id
      eName = active.name
      eAccent = active.accent
      origName = active.name
      origAccent = active.accent
      err = ''
    }
  })

  const eCustom = $derived(!PALETTES.some((x) => x.hex === eAccent))
  const dirty = $derived(!!active && (eName.trim() !== origName || eAccent !== origAccent))

  // Optimistically tint the active profile in the store so every accent-bound surface
  // (Drawer chips, the card dot) re-renders. Rolled back if the edit isn't saved.
  function tintActive(hex) {
    $profiles = {
      ...$profiles,
      list: (($profiles.list) || []).map((p) => (p.id === activeId ? { ...p, accent: hex } : p)),
    }
  }

  // Live-preview an accent: global scheme + the profile-tinted surfaces. Not persisted.
  function pickAccent(hex) {
    eAccent = hex
    setAccent(hex)
    tintActive(hex)
  }
  function pickCustom(e) {
    const v = (e.target.value || '').toLowerCase()
    if (/^#[0-9a-f]{6}$/.test(v)) pickAccent(v)
  }

  // Restore the pre-edit scheme if the live preview drifted from it.
  function rollbackAccent() {
    if (!origAccent) return
    if (getAccent() !== origAccent) setAccent(origAccent)
    if (($profiles.list || []).some((p) => p.id === activeId && p.accent !== origAccent)) tintActive(origAccent)
  }

  function reset() { eName = origName; rollbackAccent(); eAccent = origAccent; err = '' }

  // Closing Settings (unmount) with an unsaved accent preview → revert it.
  onDestroy(() => { if (eAccent !== origAccent) rollbackAccent() })

  async function save() {
    if (busy || !active) return
    const p = active
    const body = {}
    if (eName.trim() && eName.trim() !== origName) body.name = eName.trim()
    if (eAccent && eAccent !== origAccent) body.accent = eAccent
    if (!Object.keys(body).length) return
    busy = true; err = ''
    try {
      await api.updateProfile(p.id, body)
      if ('name' in body) {
        // Reflect the rename in the store list immediately.
        $profiles = { ...$profiles, list: (($profiles.list) || []).map((x) => (x.id === p.id ? { ...x, name: body.name } : x)) }
        origName = body.name
        eName = body.name
      }
      if ('accent' in body) {
        if (p.id === activeId && eAccent !== getAccent()) setAccent(eAccent)
        origAccent = eAccent
      }
      saved = true
      setTimeout(() => (saved = false), 1500)
    } catch (e) {
      err = (e && e.message) || 'Could not save profile'
    }
    busy = false
  }
</script>

{#if active}
  <!-- Identity preview — the profile as it reads elsewhere (chip monogram + name). -->
  <div class="identity" style="--dot:{eAccent}">
    <span class="idmono">{(eName || '?').trim().charAt(0).toUpperCase()}</span>
    <div class="idmeta">
      <div class="idname">{eName || 'Untitled profile'}</div>
      <div class="idpath" title={active.workspace || ''}>{active.workspace || '—'}</div>
    </div>
  </div>

  <div class="pfield">
    <label for="pg-name">Name</label>
    <input id="pg-name" bind:value={eName} placeholder="Profile name" />
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
      <label class="pswatch pcustom rainbow" class:on={eCustom} title="Custom colour">
        <input type="color" value={eAccent} oninput={pickCustom} aria-label="Custom colour" />
      </label>
    </div>
    <div class="phex">{eAccent}{#if eCustom} · custom{/if}</div>
  </div>

  {#if err}<p class="perr">{err}</p>{/if}

  <div class="pgactions">
    {#if saved}<span class="okmsg">Saved ✓</span>{/if}
    {#if dirty}<button class="linkbtn" disabled={busy} onclick={reset}>Reset</button>{/if}
    <button class="open" disabled={busy || !dirty} onclick={save}>{busy ? 'Saving…' : 'Save changes'}</button>
  </div>
{:else}
  <p class="muted">No profile selected.</p>
{/if}

<style>
  .identity {
    display: flex; align-items: center; gap: 12px;
    padding: 14px; margin-bottom: 4px;
    background: var(--surface-sunk); border: 1px solid var(--line); border-radius: var(--radius-md, 12px);
  }
  .idmono {
    width: 40px; height: 40px; flex: none; border-radius: var(--radius-pill);
    display: inline-flex; align-items: center; justify-content: center;
    background: var(--dot); color: #fff; font-weight: var(--fw-semibold); font-size: 17px;
  }
  .idmeta { min-width: 0; }
  .idname { font-size: var(--text-md, 15px); font-weight: var(--fw-semibold); }
  .idpath { font-size: var(--text-xs); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .pfield { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
  .pfield label, .plabel { font-size: var(--text-xs); font-weight: var(--fw-semibold); color: var(--text-muted); }
  .pfield input {
    border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 9px 11px;
    background-color: var(--bg); color: var(--text); font: inherit; font-size: var(--text-sm);
  }
  .pfield input:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }

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
  .pcustom.rainbow { background: conic-gradient(from 90deg, #f95339, #ec5d18, #e0b400, #2f8c44, #109e91, #2f6fe0, #7a52ec, #f95339); }
  .pcustom.rainbow.on { box-shadow: 0 0 0 2px var(--surface-sunk), 0 0 0 4px var(--accent); }
  .pcustom input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; border: none; padding: 0; background: none; }
  .phex { font-size: var(--text-xs); color: var(--text-muted); font-variant-numeric: tabular-nums; letter-spacing: .02em; margin-top: 2px; }

  .pgactions { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 16px; }
  .okmsg { color: var(--accent); font-size: var(--text-xs); }
  .perr { font-size: var(--text-sm); color: var(--danger, var(--danger)); margin: 8px 0 0; }
</style>
