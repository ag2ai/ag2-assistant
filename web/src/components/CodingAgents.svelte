<script lang="ts">
  // Read-only status of the host CLI coding agents (Claude Code / Codex / OpenCode)
  // the assistant can drive over ACP. Self-contained like McpServers/SystemHealth:
  // owns its own fetch. Editing (bridge address/token) is done via ENV/compose in
  // v1 — this card only reports what the running process sees.
  import { onMount } from 'svelte'
  import { api } from '../transport/api/index.ts'
  import { errText } from '../lib/errors.ts'
  import type { CodingAgents } from '../schemas/index.ts'
  import { m } from '../paraglide/messages.js'

  let data: CodingAgents | null = $state(null)
  let err = $state('')

  async function load() {
    err = ''
    try { data = await api.codingAgents() }
    catch (e) { err = errText(e) }
  }
  onMount(load)
</script>

<div class="setsec">
  {m.ca_title()}
  <span class="setwide" title={m.ca_host_clis_title()}>{m.ca_host_clis()}</span>
</div>

{#if err}
  <div class="ca-note ca-bad">{m.ca_load_error({ err })}</div>
{:else if !data}
  <div class="ca-note">{m.ca_checking()}</div>
{:else}
  <div class="ca-status">
    {#if data.mode === 'bridge'}
      <span class="ca-dot" class:on={data.connected} class:off={!data.connected}></span>
      {m.ca_host_bridge()} <code>{data.bridge}</code> —
      {data.connected ? m.ca_connected() : m.ca_not_reachable()}
    {:else}
      <span class="ca-dot on"></span>
      {m.ca_local()}
    {/if}
  </div>

  {#if data.mode === 'bridge' && !data.connected}
    <div class="ca-note ca-bad">
      <!-- data.error is the gateway's own words — passed through, never translated (ADR 0031). -->
      {data.error || m.ca_bridge_silent()}
      {m.ca_bridge_start()} <code>ag2-assistant acp-bridge --port 8801</code>
    </div>
  {/if}

  <ul class="ca-list">
    {#each data.agents as a (a.name)}
      <li>
        <span class="ca-name">{a.label} <span class="ca-id">({a.name})</span></span>
        <span class="ca-badge" class:ok={a.available}>{a.available ? m.ca_available() : m.ca_not_installed()}</span>
      </li>
    {:else}
      <li class="ca-empty">{m.ca_empty()}</li>
    {/each}
  </ul>

  <div class="ca-note">
    {m.ca_not_sandboxed()}
    {#if data.mode === 'local'}
      {m.ca_docker_pre()} <code>ag2-assistant acp-bridge</code> {m.ca_docker_mid()}
      <code>AG2ASSISTANT_ACP_BRIDGE</code> {m.ca_docker_post()}
    {/if}
  </div>
{/if}

<style>
  .ca-status { display: flex; align-items: center; gap: 0.5em; margin: 0.4em 0; font-size: 0.92em; }
  .ca-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--text-faint); flex: none; }
  .ca-dot.on { background: var(--success); }
  .ca-dot.off { background: var(--danger); }
  .ca-list { list-style: none; margin: 0.4em 0; padding: 0; display: flex; flex-direction: column; gap: 0.25em; }
  .ca-list li { display: flex; align-items: center; justify-content: space-between; gap: 1em;
    padding: 0.4em 0.6em; border: 1px solid var(--line); border-radius: 8px; }
  .ca-id { color: var(--text-faint); font-weight: 400; }
  .ca-badge { font-size: 0.78em; padding: 0.1em 0.55em; border-radius: 999px;
    background: var(--surface-sunk); color: var(--text-muted); white-space: nowrap; }
  .ca-badge.ok { background: color-mix(in srgb, var(--success) 15%, transparent); color: var(--success); }
  .ca-empty { justify-content: flex-start; color: var(--text-muted); }
  .ca-note { font-size: 0.82em; color: var(--text-muted); margin: 0.4em 0; line-height: 1.4; }
  .ca-bad { color: var(--danger); }
  code { font-size: 0.9em; padding: 0.05em 0.35em; border-radius: 5px;
    background: var(--surface-sunk); color: var(--text); }
</style>
