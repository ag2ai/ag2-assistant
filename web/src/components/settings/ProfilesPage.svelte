<script lang="ts">
  // Settings → Profiles: a two-level zone (ADR 0015, redesign §1-2). Level 1 is the
  // catalogue (Profiles.svelte) — a grid of profile cards. Clicking a card switches the
  // active profile to it (the whole Settings zone is scoped to the active profile) and
  // opens level 2, the ProfileEditor (header + General/Focus/Model/Folders/Memory tabs).
  // Keeping the two levels separate is the whole point: the page no longer tries to be
  // both a list AND a long stacked form.
  import { profiles } from '../../store.ts'
  import type { Profile } from '../../schemas/profile.ts'
  import { switchProfile } from '../../controller.ts'
  import { getActiveProfileId } from '../../lib/profile.ts'
  import Profiles from '../Profiles.svelte'
  import ProfileEditor from './ProfileEditor.svelte'

  let editing = $state(false)

  const activeId = $derived($profiles.activeId || getActiveProfileId())

  // Open a profile's editor. Non-active profiles are switched-to first (in place, like the
  // Drawer chips) so the editor's tabs — all scoped to the active profile — show its data.
  function open(p: Profile) {
    if (p.id !== activeId) switchProfile(p.id)
    editing = true
  }
</script>

{#if editing}
  <ProfileEditor onBack={() => (editing = false)} />
{:else}
  <div class="setgroup">Profiles</div>
  <p class="setsub">Manage how the assistant behaves in different contexts.</p>
  <Profiles onSelect={open} />
{/if}
