<script>
  // Profile editor → Skills tab (ADR 0016 t02): the skills THIS profile can draw on.
  // Two kinds of row:
  //   • inherited Bundled/Global skills — Suppress here to turn one off for THIS profile
  //     only (other profiles are untouched); an install-wide Disable shows here as an
  //     unavailable, non-suppressible row so the two surfaces never contradict.
  //   • this profile's OWN skills — Enable/Disable, scoped to the profile.
  // A change reloads only this profile (its next turn reflects it). Re-points to the
  // active profile on each switch (profileEpoch), like the Memory tab.
  import { profileEpoch } from '../../store.js'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'
  import SkillInstaller from './SkillInstaller.svelte'

  // Install targets THIS profile — the surface carries the target. Registry search is
  // target-agnostic, so it reuses the global searchSkills; discover/install are scoped.
  const installer = {
    search: api.searchSkills,
    install: api.installProfileSkill,
    discover: api.discoverProfileSkills,
    discoverUpload: api.discoverProfileSkillsUpload,
    installUpload: api.installProfileSkillUpload,
  }

  let skills = $state([])
  let loading = $state(true)
  let busy = $state(false)
  let err = $state('')
  // Two-step delete, own (Profile) skills only — arms on first click, deletes on confirm.
  let confirming = $state('')

  const load = async () => {
    loading = true; err = ''
    try { skills = (await api.profileSkills()).skills } catch (e) { err = String(e.message || e) }
    loading = false
  }
  // Re-load when the active profile changes (the tab always configures the active one).
  $effect(() => { $profileEpoch; load() })

  async function run(fn) {
    err = ''; busy = true
    try { skills = (await fn()).skills } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // Inherited (bundled/global): Suppress ⇄ un-suppress for this profile only.
  const toggleSuppress = (s) => run(() => api.suppressSkill(s.name, !s.suppressed))
  // Profile-owned: Enable/Disable for this profile (its own state).
  const toggleOwn = (s) => run(() => api.setProfileSkillState(s.name, !s.available))
  // Profile-owned: delete from disk (this profile only). Clears confirm on success.
  const del = (s) => run(() => api.deleteProfileSkill(s.name)).then(() => (confirming = ''))

  // Two sections, profile-owned first: skills this profile installed itself, then the
  // Global/Bundled skills it inherits from the app. Same source list, split by origin.
  const own = $derived(skills.filter((s) => s.origin === 'profile'))
  const inherited = $derived(skills.filter((s) => s.origin !== 'profile'))
</script>

<!-- One row, shared by both sections (SkillsPage.svelte's .setrowwrap idiom): a
     .setrow (name + description) plus its controls as siblings. The On/Off state is
     the shared .setswitch (accent when on); Delete stays an inline linkbtn confirm. -->
{#snippet skillRow(s)}
  <div class="setrowwrap" class:off={!s.available}>
    <div class="setrow">
      <span class="sk">
        {s.name}
        {#if s.origin === 'bundled'}<span class="setwide" title="First-party skill shipped with AG2 Assistant">first-party</span>{/if}
      </span>
      <span class="sv">{s.description}</span>
    </div>
    {#if confirming === s.name}
      <span class="skconfirm">Delete?</span>
      <button class="linkbtn danger" disabled={busy} onclick={() => del(s)}>Confirm</button>
      <button class="linkbtn" disabled={busy} onclick={() => (confirming = '')}>Cancel</button>
    {:else if !s.enabled}
      <!-- Off install-wide (Application → Skills): unavailable here, not this profile's to change. -->
      <span class="skblocked" title="Disabled for the whole app in Application → Skills">off app-wide</span>
    {:else if s.origin === 'profile'}
      <button class="setswitch" class:on={s.available} role="switch" aria-checked={s.available}
        title={s.available ? 'On for this profile' : 'Off for this profile'}
        disabled={busy} onclick={() => toggleOwn(s)} aria-label="{s.name} enabled"></button>
      <button class="iconbtn" title="Delete skill" aria-label="Delete skill" disabled={busy} onclick={() => (confirming = s.name)}><Icon name="trash" size={14} /></button>
    {:else}
      <button class="setswitch" class:on={!s.suppressed} role="switch" aria-checked={!s.suppressed}
        title={s.suppressed ? 'Off for this profile' : 'On for this profile'}
        disabled={busy} onclick={() => toggleSuppress(s)} aria-label="{s.name} enabled for this profile"></button>
    {/if}
  </div>
{/snippet}

<p class="muted skhint">Skills this profile can use. Turn an inherited skill off for just this profile, or disable one it owns — other profiles are unaffected.</p>

{#if err}<p class="muted skerr">{err}</p>{/if}

{#if loading}
  <p class="muted skempty">Loading…</p>
{:else}
  <div class="setgroup">This profile</div>
  <p class="setsub">Skills installed directly into this profile. Turn one off or delete it — only this profile is affected.</p>
  <!-- Install lands in THIS profile, so the "Add skill" affordance lives inside its section. -->
  <SkillInstaller {installer} onInstalled={load} />
  {#if own.length === 0}
    <p class="muted skempty">No skills installed in this profile yet.</p>
  {:else}
    {#each own as s (s.name)}{@render skillRow(s)}{/each}
  {/if}

  {#if inherited.length > 0}
    <div class="setgroup">Global &amp; bundled</div>
    <p class="setsub">Shared skills inherited from the app. Turn one off to drop it from just this profile.</p>
    {#each inherited as s (s.name)}{@render skillRow(s)}{/each}
  {/if}
{/if}

<style>
  .skhint { font-size: 12px; margin: 2px 0 10px; }
  .skerr { color: var(--danger); font-size: 13px; margin: 0 0 8px; }
  .skempty { font-size: 13px; margin: 0 0 8px; }
  /* Stack the shared rows (SkillsPage does this via .setscroll's flex gap; here
     the tab body has no such gap, so space the wrappers directly). Dim a row that
     is off for this profile — the one state the install-wide page doesn't have. */
  .setrowwrap { margin-bottom: 8px; }
  .setrowwrap.off { opacity: .55; }
  .skblocked { flex: none; font-size: 12px; color: var(--text-muted); font-style: italic; }
  .skconfirm { flex: none; font-size: 12px; color: var(--danger); }
</style>
