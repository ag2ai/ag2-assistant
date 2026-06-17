<script>
  import { onMount } from 'svelte'
  import { sessions, tasks, drawerTab } from '../store.js'
  import { route, go, newChatId } from '../router.js'
  import { api } from '../transport/api.js'

  async function refresh() {
    try { $sessions = await api.sessions() } catch {}
    try { $tasks = await api.tasksAll('all') } catch {}
  }
  onMount(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t) })

  const openChat = (id) => go('/c/' + id)
  const openTask = (id) => go('/t/' + id)
  const newChat = () => go('/c/' + newChatId())
</script>

<div class="drawer">
  <div class="dhead">
    <span class="brand">AGClaw</span>
    <button class="newbtn" onclick={newChat}>+ New</button>
  </div>
  <div class="tabs">
    <button class="tab" class:on={$drawerTab === 'chats'} onclick={() => ($drawerTab = 'chats')}>Chats</button>
    <button class="tab" class:on={$drawerTab === 'tasks'} onclick={() => ($drawerTab = 'tasks')}>Tasks</button>
  </div>

  <div class="dlist">
    {#if $drawerTab === 'chats'}
      {#if !$sessions.length}<div class="none">No conversations yet.</div>{/if}
      {#each $sessions as s (s.session_id)}
        <div class="drow" class:on={$route.name === 'chat' && $route.id === s.session_id} onclick={() => openChat(s.session_id)}>
          <div>{s.preview || s.session_id}</div>
          <div class="sub"><span>{s.turns || 0} turns</span></div>
        </div>
      {/each}
    {:else}
      {#if !$tasks.length}<div class="none">No tasks yet.</div>{/if}
      {#each $tasks as t (t.id)}
        <div class="drow" class:on={$route.name === 'task' && $route.id === t.id} onclick={() => openTask(t.id)}>
          <div>{t.title}</div>
          <div class="sub">
            <span class="badge">{t.status}</span>
            {#if t.run_of}<span class="tag">↻ run</span>{:else if t.recurrence}<span class="tag sched">⏰ {t.recurrence}</span>{/if}
          </div>
        </div>
      {/each}
    {/if}
  </div>
</div>
