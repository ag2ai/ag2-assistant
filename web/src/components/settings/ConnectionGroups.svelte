<script lang="ts">
  // Where this connection's group chats land. A group's profile cannot be set from
  // inside the group — /profile is refused there, since anyone in it can read the
  // answers — so this is the only place it moves.
  import { api } from '../../transport/api/index.ts'
  import { errText } from '../../lib/errors.ts'
  import type { Connection, ConnectionGroup, ConnectionGroups } from '../../schemas/index.ts'

  // connection: one entry from GET /api/connections. Nothing here changes the header's
  // status line, so this section never reloads the list.
  let { connection }: { connection: Connection } = $props()

  // The server's view: {groups:[{chat_id, profile}], profiles:[{id, name}]}, the
  // profiles being those reachable on THIS connection's group surface.
  let view = $state<ConnectionGroups | null>(null)
  let busy = $state('')
  let err = $state('')

  // Re-read only when the pane switches connections.
  const cid = $derived(connection.id)
  $effect(() => {
    const id = cid
    api.connectionGroups(id)
      .then((v) => { if (id === cid) view = v })
      .catch((e) => { err = errText(e) })
  })

  // A group pinned to a profile this connection cannot reach through groups is silent
  // — it is flagged rather than left looking pinned.
  const reachable = (g: ConnectionGroup) =>
    !!g.profile && !!view?.profiles.some((p) => p.id === g.profile)

  async function repoint(chatId: string, profile: string) {
    if (busy || !profile) return
    err = ''; busy = chatId
    try {
      view = await api.connectionGroupProfile(cid, chatId, profile)
    } catch (e) { err = errText(e) }
    busy = ''
  }
</script>

{#if view?.groups.length}
  <div class="setgroup">Groups</div>
  <p class="setsub">
    A group's profile is set here, not from the group — anyone in it can read the answers.
  </p>

  {#if err}<p class="cnerr">{err}</p>{/if}

  <ul class="cnlist">
    {#each view.groups as g (g.chat_id)}
      {@const ok = reachable(g)}
      <li class="cnitem">
        <span class="cnid">{g.chat_id}</span>
        {#if !ok}
          <span class="cnwarn">not reachable here — this group is silent until it is re-pointed</span>
        {/if}
        <select
          class="cngrouppick" aria-label="Profile for group {g.chat_id}"
          value={ok ? g.profile : ''} disabled={busy === g.chat_id}
          onchange={(e) => repoint(g.chat_id, e.currentTarget.value)}
        >
          {#if !ok}<option value="">Pick a profile</option>{/if}
          {#each view.profiles as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
        </select>
      </li>
    {/each}
  </ul>
{/if}
