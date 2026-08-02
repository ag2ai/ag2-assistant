<script>
  // Read-only status of the host CLI coding agents (Claude Code / Codex / OpenCode)
  // the assistant can drive over ACP. Self-contained like McpServers/SystemHealth:
  // owns its own fetch. Editing (bridge address/token) is done via ENV/compose in
  // v1 — this card only reports what the running process sees.
  import { onMount } from 'svelte'
  import { api } from '../transport/api/index.ts'

  let data = $state(null)     // {mode, bridge, connected, agents, error?}
  let err = $state('')

  async function load() {
    err = ''
    try { data = await api.codingAgents() }
    catch (e) { err = String(e?.message || e) }
  }
  onMount(load)
</script>

<div class="setsec">
  Coding agents
  <span class="setwide" title="CLI coding agents driven over ACP (Claude Code / Codex / OpenCode)">host CLIs</span>
</div>

{#if err}
  <div class="ca-note ca-bad">Couldn't load coding-agent status: {err}</div>
{:else if !data}
  <div class="ca-note">Checking…</div>
{:else}
  <div class="ca-status">
    {#if data.mode === 'bridge'}
      <span class="ca-dot" class:on={data.connected} class:off={!data.connected}></span>
      Host bridge <code>{data.bridge}</code> —
      {data.connected ? 'connected' : 'not reachable'}
    {:else}
      <span class="ca-dot on"></span>
      Running on the host — agents detected locally
    {/if}
  </div>

  {#if data.mode === 'bridge' && !data.connected}
    <div class="ca-note ca-bad">
      {data.error || 'The bridge did not respond.'}
      Start it on the host: <code>ag2-assistant acp-bridge --port 8801</code>
    </div>
  {/if}

  <ul class="ca-list">
    {#each data.agents as a (a.name)}
      <li>
        <span class="ca-name">{a.label} <span class="ca-id">({a.name})</span></span>
        <span class="ca-badge" class:ok={a.available}>{a.available ? 'available' : 'not installed'}</span>
      </li>
    {:else}
      <li class="ca-empty">No coding agents visible.</li>
    {/each}
  </ul>

  <div class="ca-note">
    Coding runs are not sandboxed — the agent edits real files in the folder you approve.
    {#if data.mode === 'local'}
      To use the host's agents from Docker, run <code>ag2-assistant acp-bridge</code> on the host and
      set <code>AG2ASSISTANT_ACP_BRIDGE</code> (see docker-compose.yml).
    {/if}
  </div>
{/if}

<style>
  .ca-status { display: flex; align-items: center; gap: 0.5em; margin: 0.4em 0; font-size: 0.92em; }
  .ca-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted, #999); flex: none; }
  .ca-dot.on { background: #29a745; }
  .ca-dot.off { background: #d9534f; }
  .ca-list { list-style: none; margin: 0.4em 0; padding: 0; display: flex; flex-direction: column; gap: 0.25em; }
  .ca-list li { display: flex; align-items: center; justify-content: space-between; gap: 1em;
    padding: 0.4em 0.6em; border: 1px solid var(--border, #e3e3e3); border-radius: 8px; }
  .ca-id { opacity: 0.6; font-weight: 400; }
  .ca-badge { font-size: 0.78em; padding: 0.1em 0.55em; border-radius: 999px;
    background: var(--muted-bg, #eee); color: var(--muted, #777); white-space: nowrap; }
  .ca-badge.ok { background: rgba(41,167,69,0.15); color: #1e7e34; }
  .ca-empty { justify-content: flex-start; opacity: 0.7; }
  .ca-note { font-size: 0.82em; opacity: 0.75; margin: 0.4em 0; line-height: 1.4; }
  .ca-bad { opacity: 1; color: #b3402f; }
  code { font-size: 0.9em; padding: 0.05em 0.35em; border-radius: 5px; background: var(--muted-bg, #f0f0f0); }
</style>
