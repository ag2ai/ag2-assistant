<script lang="ts">
  // Settings → Skills (install-wide, ADR 0016): every Bundled and Global skill the
  // agent can draw on, with an Enable/Disable toggle per row. Disabling drops a skill
  // from the <available_skills> catalog for EVERY profile from the next turn (the
  // toggle fans out a reload). Bundled skills are first-party/read-only — disable-able
  // here, but never deletable (Delete + Install land in later tickets).
  //
  // Self-contained: this list is install-wide, not part of the per-profile /api/settings
  // payload, so it loads its own data via api.skills() rather than the shared ctx.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import SkillInstaller from './SkillInstaller.svelte'
  import { errText } from '../../lib/errors.ts'
  import type { Skill } from '../../schemas/index.ts'

  // Install targets the Global (install-wide) layer — the surface carries the target.
  const installer = {
    search: api.searchSkills,
    install: api.installSkill,
    discover: api.discoverSkills,
    discoverUpload: api.discoverSkillsUpload,
    installUpload: api.installSkillUpload,
  }

  let skills = $state<Skill[]>([])
  // The skill currently being written, '' when idle. Scoped to one name rather than a
  // global flag: a global one greys out every card's switch while any single card is
  // in flight, which reads as the whole list blinking.
  let busyName = $state('')
  let err = $state('')
  // Two-step delete: first click arms the row (name), second confirms. Global only —
  // Bundled skills are read-only and never show a Delete control.
  let confirming = $state('')
  // Display order is a SNAPSHOT taken at load, not a live sort: deletable-first, then
  // enabled-before-off. Freezing it means toggling a switch dims the row in place
  // instead of yanking it to the bottom mid-interaction — the re-sort lands on reload.
  let order = $state<string[]>([])

  // deletable (non-bundled) first, then enabled before off; stable within each group.
  const sortForDisplay = (list: Skill[]) =>
    [...list].sort((a, b) =>
      (a.origin === 'bundled' ? 1 : 0) - (b.origin === 'bundled' ? 1 : 0) ||
      (a.enabled ? 0 : 1) - (b.enabled ? 0 : 1))

  const load = async () => {
    try {
      const list = (await api.skills()).skills
      skills = list
      order = sortForDisplay(list).map((s) => s.name)
    } catch (e) { err = errText(e) }
  }
  onMount(load)

  const toggle = async (s: Skill) => {
    err = ''; busyName = s.name
    try { skills = (await api.setSkillState(s.name, !s.enabled)).skills }
    catch (e) { err = errText(e) }
    busyName = ''
  }

  const del = async (s: Skill) => {
    err = ''; busyName = s.name
    try { skills = (await api.deleteSkill(s.name)).skills; confirming = '' }
    catch (e) { err = errText(e) }
    busyName = ''
  }

  // Render in the frozen `order`; toggles mutate `skills` (state/dimming) but not the
  // positions, so nothing jumps until the next load recomputes the snapshot. Names not
  // in the snapshot (shouldn't happen) fall to the end.
  const ordered = $derived.by(() => {
    const rank = new Map(order.map((n, i) => [n, i]))
    return [...skills].sort((a, b) => (rank.get(a.name) ?? 1e9) - (rank.get(b.name) ?? 1e9))
  })
</script>

<div class="skzone">
  <div class="setgroup">Skills <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
  <p class="setsub">Turn a skill off to drop it from the agent's toolkit everywhere, without deleting it. Bundled skills ship with the app and can't be removed.</p>

  {#if err}<p class="muted" style="color:var(--danger)">{err}</p>{/if}

  <!-- Sits here in the DOM; the .skzone/.skadd idiom in app.css pins it onto the
       header line while collapsed and drops it back here when it expands. -->
  <div class="skadd"><SkillInstaller {installer} onInstalled={load} /></div>

  <!-- One .skcard per skill (idiom in app.css, shared with the per-profile tab). -->
  <div class="sklist">
    {#if skills.length === 0}
      <p class="muted">No skills installed.</p>
    {/if}

    <!-- .sktop IS the switch: name, description and the pill are one widget, so the whole
         line is the target rather than the 34px toggle. The pill is decorative; the meta
         line's buttons sit outside the widget, so no interactive element is nested in it. -->
    {#each ordered as s (s.name)}
      <!-- canToggle depends only on THIS row — a write elsewhere must not restyle every
           other card. Armed for delete counts as off: a stray click shouldn't toggle a
           skill you're about to remove. -->
      {@const rowBusy = busyName === s.name}
      {@const canToggle = !rowBusy && confirming !== s.name}
      <div class="skcard" class:off={!s.enabled}>
        <div
          class="sktop" class:clickable={canToggle}
          role="switch" aria-checked={s.enabled} aria-disabled={!canToggle}
          tabindex={canToggle ? 0 : -1}
          aria-labelledby="sk-{s.name}" aria-describedby="skd-{s.name}"
          title={canToggle ? (s.enabled ? 'Click to turn off' : 'Click to turn on') : ''}
          onclick={() => { if (canToggle) toggle(s) }}
          onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && canToggle) { e.preventDefault(); toggle(s) } }}
        >
          <div class="skmain">
            <span class="skname" id="sk-{s.name}">{s.name}</span>
            <p class="skdesc" id="skd-{s.name}">{s.description}</p>
          </div>
          <div class="skctl">
            <span class="setswitch" class:on={s.enabled} class:busy={rowBusy} aria-hidden="true"></span>
          </div>
        </div>
        <div class="skmeta">
          {#if confirming === s.name}
            <span class="skconfirm">Delete {s.name}?</span>
            <button class="linkbtn danger skmetabtn" disabled={rowBusy} onclick={() => del(s)}>Confirm</button>
            <button class="linkbtn" disabled={rowBusy} onclick={() => (confirming = '')}>Cancel</button>
          {:else}
            <span class="skdot" class:third={s.origin !== 'bundled'}></span>
            {s.origin === 'bundled' ? 'First-party · ships with the app' : 'Installed · global'}
            {#if s.origin !== 'bundled'}
              <button class="linkbtn quiet skmetabtn" disabled={rowBusy} onclick={() => (confirming = s.name)}>Delete</button>
            {/if}
          {/if}
        </div>
      </div>
    {/each}
  </div>
</div>
