<script lang="ts">
  // One connection's Profiles table: which profiles it can reach, per surface, and
  // which of them its conversations land in by default. One row per profile; the
  // server refuses the same unreachable-default state the radio does.
  import { api } from '../../transport/api/index.ts'
  import { reachableAnywhere, surfaceLabel } from '../../lib/integrations.ts'
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type { Connection, ConnectionExposure, Profile } from '../../schemas/index.ts'

  // connection: one entry from GET /api/connections. reload: re-fetch the list, called
  // whenever the default moves, since the header's status line is derived from it.
  type Props = {
    connection: Connection
    profById?: Record<string, Profile | undefined>
    reload: () => Promise<void>
  }
  let { connection, profById = {}, reload }: Props = $props()

  // The server's view: {surfaces:[{kind, id}], exposure:{pid:{surface_id: bool}},
  // default_profile}. Unarchived profiles, in registry order — the row order here.
  let view = $state<ConnectionExposure | null>(null)
  let busy = $state(false)
  let err = $state('')

  const rows = $derived(Object.keys(view?.exposure || {}))
  const anyWithdrawn = $derived(rows.some((pid) => !reachableAnywhere(view?.exposure, pid)))

  // Re-read only when the pane switches connections, not on every list reload.
  const cid = $derived(connection.id)
  $effect(() => {
    const id = cid
    api.connectionExposure(id)
      .then((v) => { if (id === cid) view = v })
      .catch((e) => { err = errText(e) })
  })

  async function toggle(pid: string, surface: string) {
    if (busy || !view) return
    err = ''; busy = true
    const was = view.default_profile
    try {
      const on = view.exposure[pid]?.[surface] !== false
      view = await api.setConnectionExposure(connection.id, pid, surface, !on)
      // A withdrawal can take the default's last surface, and the server clears it.
      if (view.default_profile !== was) await reload()
    } catch (e) { err = errText(e) }
    busy = false
  }

  // Clicking the profile that is already the default clears it — how "no default" stays
  // reachable now that it has no row of its own.
  async function setDefault(pid: string) {
    if (busy || !view) return
    err = ''; busy = true
    try {
      const entry = await api.connectionDefault(connection.id, view.default_profile === pid ? null : pid)
      view = { ...view, default_profile: entry.default_profile }
      await reload()
    } catch (e) { err = errText(e) }
    busy = false
  }
</script>

<div class="setgroup">{m.settings_page_profiles()}</div>
<p class="setsub">
  {m.integrations_profiles_lead()}
</p>

{#if err}<p class="cnerr">{err}</p>{/if}

{#if view}
  <table class="cnexp">
    <thead>
      <tr>
        <th>{m.integrations_col_profile()}</th>
        <th class="cnexpdef">{m.integrations_col_default()}</th>
        {#each view.surfaces as s (s.id)}
          <th class="cnexpcol">{surfaceLabel(connection.platform, s.kind)}</th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as pid (pid)}
        {@const p = profById[pid]}
        {@const reach = reachableAnywhere(view.exposure, pid)}
        {@const name = p?.name || pid}
        {@const isDefault = view.default_profile === pid}
        <tr>
          <td class="cnexpname">
            <span class="cnexpdot" style="--dot:{p?.accent || 'var(--muted)'}"></span>
            <span class:dim={!reach}>{name}</span>
          </td>
          <td class="cnexpdef">
            <!-- A button, not <input type=radio>: a native radio group cannot be emptied
                 by clicking the checked one, and "no default" has to stay reachable. -->
            <button
              class="cnradio" class:on={isDefault} role="radio" aria-checked={isDefault}
              disabled={busy || !reach}
              aria-label={m.integrations_default_aria({ name, connection: connection.name })}
              title={!reach
                ? m.integrations_withdrawn_title()
                : isDefault
                  ? m.integrations_clear_default_title()
                  : m.integrations_make_default_title()}
              onclick={() => setDefault(pid)}
            ></button>
          </td>
          {#each view.surfaces as s (s.id)}
            {@const on = view.exposure[pid]?.[s.id] !== false}
            <td class="cnexpcol">
              <button
                class="setswitch" class:on role="switch" aria-checked={on} disabled={busy}
                title={on ? m.integrations_reachable_title() : m.integrations_withdrawn_surface_title()}
                aria-label={m.integrations_reachable_aria({ name, connection: connection.name, surface: surfaceLabel(connection.platform, s.kind) })}
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
      {m.integrations_withdrawn_hint()}
    </p>
  {/if}
  {#if view.default_profile == null}
    <p class="cnwarn">{m.integrations_no_default_warn()}</p>
  {/if}
{/if}
