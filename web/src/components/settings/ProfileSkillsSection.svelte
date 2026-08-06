<script lang="ts">
  // Profile editor → Skills tab (ADR 0016 t02): the skills THIS profile can draw on.
  // Two kinds of row:
  //   • inherited Bundled/Global skills — Suppress here to turn one off for THIS profile
  //     only (other profiles are untouched); an install-wide Disable shows here as an
  //     unavailable, non-suppressible row so the two surfaces never contradict.
  //   • this profile's OWN skills — Enable/Disable, scoped to the profile.
  // A change reloads only this profile (its next turn reflects it). Re-points to the
  // active profile on each switch (profileEpoch), like the Memory tab.
  import { profileEpoch } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import SkillInstaller from './SkillInstaller.svelte'
  import { errText } from '../../lib/errors.ts'
  import type { ProfileSkill } from '../../schemas/index.ts'

  // Install targets THIS profile — the surface carries the target. Registry search is
  // target-agnostic, so it reuses the global searchSkills; discover/install are scoped.
  const installer = {
    search: api.searchSkills,
    install: api.installProfileSkill,
    discover: api.discoverProfileSkills,
    discoverUpload: api.discoverProfileSkillsUpload,
    installUpload: api.installProfileSkillUpload,
  }

  let skills = $state<ProfileSkill[]>([])
  let loading = $state(true)
  // The skill currently being written, '' when idle. Scoped to one name rather than a
  // global flag: a global one greys out every card's switch while any single card is
  // in flight, which reads as the whole list blinking.
  let busyName = $state('')
  const busy = $derived(busyName !== '')
  let err = $state('')
  // Two-step delete, own (Profile) skills only — arms on first click, deletes on confirm.
  let confirming = $state('')

  const load = async () => {
    loading = true; err = ''
    try { skills = (await api.profileSkills()).skills } catch (e) { err = errText(e) }
    loading = false
  }
  // Re-load when the active profile changes (the tab always configures the active one).
  $effect(() => { $profileEpoch; load() })

  async function run(name: string, fn: () => Promise<{ skills: ProfileSkill[] }>) {
    err = ''; busyName = name
    try { skills = (await fn()).skills } catch (e) { err = errText(e) }
    busyName = ''
  }

  // Inherited (bundled/global): Suppress ⇄ un-suppress for this profile only.
  const toggleSuppress = (s: ProfileSkill) => run(s.name, () => api.suppressSkill(s.name, !s.suppressed))
  // Profile-owned: Enable/Disable for this profile (its own state).
  const toggleOwn = (s: ProfileSkill) => run(s.name, () => api.setProfileSkillState(s.name, !s.available))
  // Profile-owned: delete from disk (this profile only). Clears confirm on success.
  const del = (s: ProfileSkill) => run(s.name, () => api.deleteProfileSkill(s.name)).then(() => (confirming = ''))
  // Clicking the card flips whichever switch that card shows.
  const toggleCard = (s: ProfileSkill) => (s.origin === 'profile' ? toggleOwn(s) : toggleSuppress(s))

  // Two sections, profile-owned first: skills this profile installed itself, then the
  // Global/Bundled skills it inherits from the app. Same source list, split by origin.
  const own = $derived(skills.filter((s) => s.origin === 'profile'))
  const inherited = $derived(skills.filter((s) => s.origin !== 'profile'))
</script>

<!-- One .skcard per skill (idiom in app.css, shared with the install-wide page): name
     and description, the .setswitch, then provenance + Delete on the meta line. -->
{#snippet skillRow(s: ProfileSkill)}
  <!-- Click anywhere on the card to flip its switch. Not when it's armed for delete,
       and not when the skill is off app-wide — that card has no switch to flip. -->
  {@const rowBusy = busyName === s.name}
  {@const canToggle = !rowBusy && confirming !== s.name && s.enabled}
  <div
    class="skcard" class:off={!s.available} class:clickable={canToggle}
    role="button" aria-disabled={!canToggle} tabindex={canToggle ? 0 : -1}
    aria-label="{s.available ? 'Turn off' : 'Turn on'} {s.name} for this profile"
    title={canToggle ? (s.available ? 'Click to turn off for this profile' : 'Click to turn on for this profile') : ''}
    onclick={() => { if (canToggle && !busy) toggleCard(s) }}
    onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && canToggle && !busy) { e.preventDefault(); toggleCard(s) } }}
  >
    <div class="sktop">
      <div class="skmain">
        <span class="skname">{s.name}</span>
        <p class="skdesc">{s.description}</p>
      </div>
      <div class="skctl">
        {#if !s.enabled}
          <!-- Off install-wide: not this profile's to change, so no switch at all. -->
          <span class="skblocked" title="Disabled for the whole app in Application → Skills">off app-wide</span>
        {:else if s.origin === 'profile'}
          <button class="setswitch" class:on={s.available} role="switch" aria-checked={s.available}
            title={s.available ? 'On for this profile' : 'Off for this profile'}
            disabled={rowBusy} onclick={(e) => { e.stopPropagation(); toggleOwn(s) }} aria-label="{s.name} enabled"></button>
        {:else}
          <button class="setswitch" class:on={!s.suppressed} role="switch" aria-checked={!s.suppressed}
            title={s.suppressed ? 'Off for this profile' : 'On for this profile'}
            disabled={rowBusy} onclick={(e) => { e.stopPropagation(); toggleSuppress(s) }} aria-label="{s.name} enabled for this profile"></button>
        {/if}
      </div>
    </div>
    <div class="skmeta">
      {#if confirming === s.name}
        <span class="skconfirm">Delete {s.name}?</span>
        <button class="linkbtn danger skmetabtn" disabled={rowBusy} onclick={(e) => { e.stopPropagation(); del(s) }}>Confirm</button>
        <button class="linkbtn" disabled={rowBusy} onclick={(e) => { e.stopPropagation(); confirming = '' }}>Cancel</button>
      {:else}
        <!-- Where the skill comes from, and so whose it is to change. -->
        <span class="skdot" class:third={s.origin !== 'bundled'}></span>
        {s.origin === 'bundled' ? 'First-party · ships with the app'
          : s.origin === 'profile' ? 'This profile · installed here'
          : 'Installed · global'}
        {#if s.origin === 'profile'}
          <button class="linkbtn quiet skmetabtn" disabled={rowBusy} onclick={(e) => { e.stopPropagation(); confirming = s.name }}>Delete</button>
        {/if}
      {/if}
    </div>
  </div>
{/snippet}

<p class="muted skhint">Skills this profile can use. Turn an inherited skill off for just this profile, or disable one it owns — other profiles are unaffected.</p>

{#if err}<p class="muted skerr">{err}</p>{/if}

{#if loading}
  <p class="muted skempty">Loading…</p>
{:else}
  <!-- Install lands in THIS profile, so Add skill belongs to this section's header
       line; .skzone/.skadd pin it there while collapsed (idiom in app.css). -->
  <div class="skzone">
    <div class="setgroup">This profile</div>
    <p class="setsub">Skills installed directly into this profile. Turn one off or delete it — only this profile is affected.</p>
    <div class="skadd"><SkillInstaller {installer} onInstalled={load} /></div>
    <div class="sklist">
      {#if own.length === 0}
        <p class="muted skempty">No skills installed in this profile yet.</p>
      {:else}
        {#each own as s (s.name)}{@render skillRow(s)}{/each}
      {/if}
    </div>
  </div>

  {#if inherited.length > 0}
    <div class="setgroup">Global &amp; bundled</div>
    <p class="setsub">Shared skills inherited from the app. Turn one off to drop it from just this profile.</p>
    <div class="sklist">
      {#each inherited as s (s.name)}{@render skillRow(s)}{/each}
    </div>
  {/if}
{/if}

<style>
  .skhint { font-size: 12px; margin: 2px 0 10px; }
  .skerr { color: var(--danger); font-size: 13px; margin: 0 0 8px; }
  .skempty { font-size: 13px; margin: 0 0 8px; }
  /* Stands in for the switch on a skill that's off install-wide — the one state the
     install-wide page can't have. Card/list/Add-skill idioms live in app.css. */
  .skblocked { flex: none; font-size: 12px; color: var(--text-muted); font-style: italic; }
</style>
