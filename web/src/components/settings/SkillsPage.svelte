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
  import Icon from '../Icon.svelte'
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
  let busy = $state(false)
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
    err = ''; busy = true
    try { skills = (await api.setSkillState(s.name, !s.enabled)).skills }
    catch (e) { err = errText(e) }
    busy = false
  }

  const del = async (s: Skill) => {
    err = ''; busy = true
    try { skills = (await api.deleteSkill(s.name)).skills; confirming = '' }
    catch (e) { err = errText(e) }
    busy = false
  }

  // Render in the frozen `order`; toggles mutate `skills` (state/dimming) but not the
  // positions, so nothing jumps until the next load recomputes the snapshot. Names not
  // in the snapshot (shouldn't happen) fall to the end.
  const ordered = $derived.by(() => {
    const rank = new Map(order.map((n, i) => [n, i]))
    return [...skills].sort((a, b) => (rank.get(a.name) ?? 1e9) - (rank.get(b.name) ?? 1e9))
  })
</script>

<div class="setgroup">Skills <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<p class="setsub">Turn a skill off to drop it from the agent's toolkit everywhere, without deleting it. Bundled skills ship with the app and can't be removed.</p>

<SkillInstaller {installer} onInstalled={load} />

{#if err}<p class="muted" style="color:var(--danger)">{err}</p>{/if}

{#if skills.length === 0}
  <p class="muted">No skills installed.</p>
{:else}
  <!-- One .setrowwrap PER skill: it's a horizontal wrapper (a .setrow + its action
       buttons as siblings), not a vertical list container — the rows stack because each
       wrapper is its own block. Putting the whole {#each} in one .setrowwrap laid every
       skill out side-by-side (flex:1) and clipped them. -->
  {#each ordered as s (s.name)}
    <div class="setrowwrap" class:off={!s.enabled}>
      <div class="setrow">
        <span class="sk">
          {s.name}
          {#if s.origin === 'bundled'}<span class="setwide" title="First-party skill shipped with AG2 Assistant">first-party</span>{/if}
        </span>
        <span class="sv">{s.description}</span>
      </div>
      {#if confirming === s.name}
        <span class="skconfirm">Delete {s.name}?</span>
        <button class="linkbtn danger" disabled={busy} onclick={() => del(s)}>Confirm</button>
        <button class="linkbtn" disabled={busy} onclick={() => (confirming = '')}>Cancel</button>
      {:else}
        <button class="setswitch" class:on={s.enabled} role="switch" aria-checked={s.enabled}
          title={s.enabled ? 'On — available everywhere' : 'Off — dropped from every profile'}
          disabled={busy} onclick={() => toggle(s)} aria-label="{s.name} enabled"></button>
        {#if s.origin !== 'bundled'}
          <button class="iconbtn" title="Delete skill" aria-label="Delete skill" disabled={busy} onclick={() => (confirming = s.name)}><Icon name="trash" size={14} /></button>
        {/if}
      {/if}
    </div>
  {/each}
{/if}

<style>
  .skconfirm { font-size: 12px; color: var(--danger); }
  /* Off skills read as disabled — dimmed in place (same idiom as the profile tab).
     The switch itself stays full-strength so it's still an obvious re-enable target. */
  .setrowwrap.off .setrow { opacity: .5; }
</style>
