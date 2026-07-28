<script>
  // Profile editor → Channels tab: this profile's Channel exposure — which surfaces it
  // is reachable from. Default-allow, so every switch starts on; turning one off
  // withdraws the profile there and takes effect on the next platform message.
  import { profiles } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { getActiveProfileId } from '../../lib/profile.js'

  // Surface ids are the backend's (assistant/profiles.py CHANNEL_SURFACES); the labels
  // are the frontend's. Telegram's direct messages and groups switch independently.
  const SURFACES = [
    { id: 'telegram:dm', label: 'Telegram', what: 'direct messages' },
    { id: 'telegram:group', label: 'Telegram', what: 'groups' },
    { id: 'discord', label: 'Discord', what: 'servers and direct messages' },
    { id: 'slack', label: 'Slack', what: 'channels and direct messages' },
  ]

  const list = $derived($profiles.list || [])
  const activeId = $derived($profiles.activeId || getActiveProfileId())
  const active = $derived(list.find((p) => p.id === activeId) || null)
  const exposure = $derived(active?.exposure || {})

  let busy = $state(false)
  let err = $state('')

  async function toggle(surface) {
    if (busy || !active) return
    busy = true; err = ''
    try {
      const { profile } = await api.setProfileExposure(active.id, surface, exposure[surface] === false)
      $profiles = { ...$profiles, list: list.map((p) => (p.id === profile.id ? { ...p, ...profile } : p)) }
    } catch (e) {
      err = (e && e.message) || 'Could not change where this profile is reachable'
    }
    busy = false
  }
</script>

{#if active}
  <p class="setsub" style="margin:0 0 10px">Where this profile can be talked to. A profile is reachable everywhere until you turn a surface off; a conversation sitting in it is told rather than moved.</p>

  {#each SURFACES as s (s.id)}
    <div class="setrowwrap">
      <div class="setrow">
        <span class="sk">{s.label}</span>
        <span class="sv">{s.what}</span>
      </div>
      <button class="setswitch" class:on={exposure[s.id] !== false} role="switch"
        aria-checked={exposure[s.id] !== false}
        title={exposure[s.id] === false ? 'Withdrawn from this surface' : 'Reachable here'}
        aria-label="{active.name} reachable from {s.label} {s.what}"
        disabled={busy} onclick={() => toggle(s.id)}></button>
    </div>
  {/each}

  {#if err}<p class="perr">{err}</p>{/if}
{:else}
  <p class="muted">No profile selected.</p>
{/if}

<style>
  .perr { font-size: var(--text-sm); color: var(--danger); margin: 8px 0 0; }
</style>
