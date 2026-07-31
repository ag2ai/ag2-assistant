<script>
  // One connection's Profiles table: which profiles it can reach, per surface, and
  // which of them its conversations land in by default. The two used to be separate
  // controls in separate places (Profile editor → Channels, and the channel default),
  // and separating them let the table lie — a default nothing could reach. One row per
  // profile, and the server refuses the same state the radio does.
  import { api } from '../../transport/api.js'
  import { reachableAnywhere, surfaceLabel } from '../../lib/integrations.js'

  // connection: one entry from GET /api/connections. reload: re-fetch the list, called
  // whenever the default moves, since the header's status line is derived from it.
  let { connection, profById = {}, reload } = $props()

  // The server's view: {surfaces:[{kind, id}], exposure:{pid:{surface_id: bool}},
  // default_profile}. Unarchived profiles, in registry order — the row order here.
  let view = $state(null)
  let busy = $state(false)
  let err = $state('')

  const rows = $derived(Object.keys(view?.exposure || {}))
  const anyWithdrawn = $derived(rows.some((pid) => !reachableAnywhere(view.exposure, pid)))

  // Re-read only when the pane switches connections, not on every list reload.
  const cid = $derived(connection.id)
  $effect(() => {
    const id = cid
    api.connectionExposure(id)
      .then((v) => { if (id === cid) view = v })
      .catch((e) => { err = String(e.message || e) })
  })

  async function toggle(pid, surface) {
    if (busy || !view) return
    err = ''; busy = true
    const was = view.default_profile
    try {
      view = await api.setConnectionExposure(connection.id, pid, surface, view.exposure[pid][surface] === false)
      // A withdrawal can take the default's last surface, and the server clears it.
      if (view.default_profile !== was) await reload()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // Clicking the profile that is already the default clears it — how "no default" stays
  // reachable now that it has no row of its own.
  async function setDefault(pid) {
    if (busy || !view) return
    err = ''; busy = true
    try {
      const entry = await api.connectionDefault(connection.id, view.default_profile === pid ? null : pid)
      view = { ...view, default_profile: entry.default_profile }
      await reload()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }
</script>

<div class="setgroup">Profiles</div>
<p class="setsub">
  Every profile is reachable through this connection until you turn it off; a conversation
  already sitting in a withdrawn one is told, not moved. The default is where a new
  conversation lands when nothing else has been chosen.
</p>

{#if err}<p class="cnerr">{err}</p>{/if}

{#if view}
  <table class="cnexp">
    <thead>
      <tr>
        <th>Profile</th>
        <th class="cnexpdef">Default</th>
        {#each view.surfaces as s (s.id)}
          <th class="cnexpcol">{surfaceLabel(connection.platform, s.kind)}</th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as pid (pid)}
        {@const p = profById[pid] || {}}
        {@const reach = reachableAnywhere(view.exposure, pid)}
        {@const name = p.name || pid}
        {@const isDefault = view.default_profile === pid}
        <tr>
          <td class="cnexpname">
            <span class="cnexpdot" style="--dot:{p.accent || 'var(--muted)'}"></span>
            <span class:dim={!reach}>{name}</span>
          </td>
          <td class="cnexpdef">
            <!-- A button, not <input type=radio>: a native radio group cannot be emptied
                 by clicking the checked one, and "no default" has to stay reachable. -->
            <button
              class="cnradio" class:on={isDefault} role="radio" aria-checked={isDefault}
              disabled={busy || !reach}
              aria-label="{name} is the default profile for {connection.name}"
              title={!reach
                ? 'Withdrawn from every surface — it cannot be the default'
                : isDefault
                  ? 'Leave this connection with no default'
                  : 'Make this the default profile'}
              onclick={() => setDefault(pid)}
            ></button>
          </td>
          {#each view.surfaces as s (s.id)}
            {@const on = view.exposure[pid][s.id] !== false}
            <td class="cnexpcol">
              <button
                class="setswitch" class:on role="switch" aria-checked={on} disabled={busy}
                title={on ? 'Reachable here' : 'Withdrawn from this surface'}
                aria-label="{name} reachable from {connection.name} {surfaceLabel(connection.platform, s.kind)}"
                onclick={() => toggle(pid, s.id)}
              ></button>
            </td>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>

  {#if anyWithdrawn}
    <p class="cnhint">
      A greyed-out profile is withdrawn from every surface here, so it cannot be the default.
    </p>
  {/if}
  {#if view.default_profile == null}
    <p class="cnwarn">No default — a conversation that has not been pointed anywhere is refused.</p>
  {/if}
{/if}
