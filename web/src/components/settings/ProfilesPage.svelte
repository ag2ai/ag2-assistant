<script>
  // Settings → Profiles: the profile list and focus areas. (Folder access moved to
  // Settings → Folders — the install-wide Folder registry + Grants, ADR 0006.)
  import { getSettings } from './context.svelte.js'
  import { api } from '../../transport/api.js'
  import { FOCUS } from '../../lib/focuses.js'
  import Icon from '../Icon.svelte'
  import Profiles from '../Profiles.svelte'

  const ctx = getSettings()

  // Focus areas — a per-profile persona attribute (settings.json → agent context).
  // Toggling a pill persists immediately for the ACTIVE profile.
  const toggleFocus = (id) => {
    const cur = ctx.s?.focuses || []
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    ctx.run(() => api.setFocuses(next))
  }
</script>

<div class="setsec">Profiles</div>
<Profiles />

<div class="setsec">Focus areas</div>
<div class="focuspills">
  {#each FOCUS as f}
    <button class="focuspill" class:on={(ctx.s.focuses || []).includes(f.id)} disabled={ctx.busy} onclick={() => toggleFocus(f.id)}>
      <Icon name={f.icon} size={13} /> {f.label}
    </button>
  {/each}
</div>
<p class="muted" style="font-size:12px;margin:2px 0 0">What this profile is for — shapes how the assistant helps.</p>
