<script lang="ts">
  // Profiles → Focus areas section: the focus pills. A per-profile persona attribute;
  // toggling a pill persists immediately for the active profile via setFocuses.
  import { getSettings } from './context.svelte.ts'
  import { api } from '../../transport/api/index.ts'
  import { FOCUS } from '../../lib/focuses.ts'
  import { m } from '../../paraglide/messages.js'
  import Icon from '../Icon.svelte'

  const ctx = getSettings()

  const toggleFocus = (id: string) => {
    const cur = ctx.s?.focuses || []
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]
    ctx.run(() => api.setFocuses(next))
  }
</script>

<div class="focuspills">
  {#each FOCUS as f}
    <button class="focuspill" class:on={(ctx.s?.focuses || []).includes(f.id)} disabled={ctx.busy} onclick={() => toggleFocus(f.id)}>
      <Icon name={f.icon} size={13} /> {f.label}
    </button>
  {/each}
</div>
<p class="setsub" style="margin:4px 0 0">{m.profile_focus_hint()}</p>
