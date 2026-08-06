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
  let busy = $state(false)
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

  async function run(fn: () => Promise<{ skills: ProfileSkill[] }>) {
    err = ''; busy = true
    try { skills = (await fn()).skills } catch (e) { err = errText(e) }
    busy = false
  }

  // Inherited (bundled/global): Suppress ⇄ un-suppress for this profile only.
  const toggleSuppress = (s: ProfileSkill) => run(() => api.suppressSkill(s.name, !s.suppressed))
  // Profile-owned: Enable/Disable for this profile (its own state).
  const toggleOwn = (s: ProfileSkill) => run(() => api.setProfileSkillState(s.name, !s.available))
  // Profile-owned: delete from disk (this profile only). Clears confirm on success.
  const del = (s: ProfileSkill) => run(() => api.deleteProfileSkill(s.name)).then(() => (confirming = ''))

  // Two sections, profile-owned first: skills this profile installed itself, then the
  // Global/Bundled skills it inherits from the app. Same source list, split by origin.
  const own = $derived(skills.filter((s) => s.origin === 'profile'))
  const inherited = $derived(skills.filter((s) => s.origin !== 'profile'))
</script>

<!-- One .skcard per skill (idiom in app.css, shared with the install-wide page): name
     and description, the .setswitch, then provenance + Delete on the meta line. -->
{#snippet skillRow(s: ProfileSkill)}
  <div class="skcard" class:off={!s.available}>
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
            disabled={busy} onclick={() => toggleOwn(s)} aria-label="{s.name} enabled"></button>
        {:else}
          <button class="setswitch" class:on={!s.suppressed} role="switch" aria-checked={!s.suppressed}
            title={s.suppressed ? 'Off for this profile' : 'On for this profile'}
            disabled={busy} onclick={() => toggleSuppress(s)} aria-label="{s.name} enabled for this profile"></button>
        {/if}
      </div>
    </div>
    <div class="skmeta">
      {#if confirming === s.name}
        <span class="skconfirm">Delete {s.name}?</span>
        <button class="linkbtn danger skmetabtn" disabled={busy} onclick={() => del(s)}>Confirm</button>
        <button class="linkbtn" disabled={busy} onclick={() => (confirming = '')}>Cancel</button>
      {:else}
        <!-- Where the skill comes from, and so whose it is to change. -->
        <span class="skdot" class:third={s.origin !== 'bundled'}></span>
        {s.origin === 'bundled' ? 'First-party · ships with the app'
          : s.origin === 'profile' ? 'This profile · installed here'
          : 'Installed · global'}
        {#if s.origin === 'profile'}
          <button class="linkbtn quiet skmetabtn" disabled={busy} onclick={() => (confirming = s.name)}>Delete</button>
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
