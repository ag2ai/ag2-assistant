<script lang="ts">
  // The system-health status dot (main top-right, in the thread header cluster).
  // A little circle that reflects overall health at a glance — green/amber/red —
  // and opens a panel listing each subsystem's state. Self-contained like
  // McpServers.svelte / Channels.svelte: owns its own state and poll.
  //
  // The dot polls the CHEAP aggregate (GET .../health): agent liveness, provider
  // key, channel start-errors — no subprocess spawns. MCP servers are expensive to
  // probe (each spawns a process that can hang), so they're checked only when the
  // panel is OPENED, and a server already known-down is NOT re-probed on reopen —
  // it shows its last error with a Recheck button that re-probes just that one.
  import { onMount } from 'svelte'
  import { api } from '../transport/api/index.ts'
  import { errText } from '../lib/errors.ts'
  import type { HealthState, ProfileHealth } from '../schemas/index.ts'
  import Icon from './Icon.svelte'

  // One open shape rather than a union of the three probe states: the markup reads
  // mcp[name], and element access with a computed key never narrows a union.
  type McpProbe = { checking?: boolean; ok?: boolean; tools?: string[]; error?: string }

  let data = $state<ProfileHealth | null>(null)   // from the cheap poll
  let open = $state(false)
  let root: HTMLDivElement | undefined = $state() // popover container (click-outside)
  // name -> probe state — cached across opens so healthy servers aren't re-probed
  // needlessly and down ones stay put until an explicit Recheck.
  let mcp: Record<string, McpProbe | undefined> = $state({})

  // The MCP check block, if any (its .servers drive the per-server list).
  const mcpCheck = $derived((data?.checks || []).find((c) => c.id === 'mcp'))

  // Worst of the cheap overall and any probed-down MCP server. A down MCP server
  // escalates the dot to amber (MCP is auxiliary — never red); before any probe it
  // doesn't affect the dot at all.
  const RANK: Record<HealthState, number> = { ok: 0, off: 0, warn: 1, down: 2 }
  const effective = $derived.by(() => {
    let s = data?.overall || 'ok'
    // Only count MCP results for servers that STILL exist. The `mcp` probe cache
    // outlives the config, so a deleted server's cached failure would otherwise
    // keep the dot amber even though its panel row is already gone.
    const names = new Set((mcpCheck?.servers || []).map((x) => x.name))
    const anyDown = Object.entries(mcp).some(([n, h]) => names.has(n) && h && h.ok === false)
    if (anyDown && RANK[s] < 1) s = 'warn'
    return s
  })

  const TIP: Record<string, string | undefined> = {
    ok: 'All systems healthy',
    warn: 'Needs attention — click for details',
    down: 'Problem — click for details',
  }

  async function refresh() {
    try { data = await api.health() } catch {}
  }

  // Probe every configured server whose cached state is NOT known-down. A down
  // server is left as-is (its Recheck button re-probes just it). Runs in parallel.
  function checkMcp() {
    const servers = mcpCheck?.servers || []
    for (const s of servers) {
      if (s.enabled === false) continue
      if (mcp[s.name]?.ok === false) continue // known-down: don't re-spawn
      probe(s.name)
    }
  }

  async function probe(name: string) {
    // Mutate the key in place, NOT `mcp = {...mcp, [name]: v}`. Probes run
    // concurrently (a fast ENOENT server + a slow one that spawns a process), and a
    // whole-object read-modify-write lets the slower probe's write clobber the
    // faster one's already-stored result. Per-key assignment on the $state proxy is
    // race-free.
    mcp[name] = { checking: true }
    try {
      mcp[name] = await api.healthMcpServer(name)
    } catch (e) {
      mcp[name] = { ok: false, error: errText(e) }
    }
  }

  function toggle() {
    open = !open
    if (open) checkMcp()
  }

  function onDocPointer(e: PointerEvent) {
    if (open && root && e.target instanceof Node && !root.contains(e.target)) open = false
  }
  function onDocKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') open = false
  }

  onMount(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    document.addEventListener('pointerdown', onDocPointer, true)
    document.addEventListener('keydown', onDocKey)
    return () => {
      clearInterval(t)
      document.removeEventListener('pointerdown', onDocPointer, true)
      document.removeEventListener('keydown', onDocKey)
    }
  })
</script>

<div class="sh" bind:this={root}>
  <button
    class="shdot state-{effective}"
    class:open
    title={TIP[effective] || 'System health'}
    aria-label="System health"
    aria-expanded={open}
    onclick={toggle}
  >
    <span class="dot"></span>
  </button>

  {#if open}
    <div class="shpanel" role="menu">
      <div class="shhead">System health</div>
      {#if !data}
        <div class="shempty">Checking…</div>
      {:else}
        {#each data.checks as c (c.id)}
          <div class="shrow">
            <span class="rowdot state-{c.state}"></span>
            <div class="shtext">
              <div class="shlabel">{c.label}</div>
              {#if c.detail}<div class="shdetail">{c.detail}</div>{/if}
            </div>
          </div>
          {#if c.id === 'mcp' && (c.servers || []).length}
            <div class="shservers">
              {#each c.servers as s (s.name)}
                {@const h = mcp[s.name]}
                <div class="shsrv">
                  <span class="rowdot state-{h?.ok ? 'ok' : h?.ok === false ? 'down' : 'off'}"></span>
                  <span class="srvname" title={s.name}>{s.name}</span>
                  <span class="srvstat" class:bad={h && h.ok === false}>
                    {#if s.enabled === false}disabled
                    {:else if !h}—
                    {:else if h.checking}checking…
                    {:else if h.ok}healthy · {(h.tools || []).length} tools
                    {:else}{h.error}{/if}
                  </span>
                  {#if s.enabled !== false && !(h && h.checking)}
                    <button class="recheck" onclick={() => probe(s.name)} title="Recheck this server">
                      <Icon name="rotate-cw" size={12} />
                    </button>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .sh { position: relative; display: inline-flex; align-items: center; }

  /* The dot button — a quiet circle that carries the health colour. */
  .shdot {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; padding: 0;
    border: none; background: none; cursor: pointer; border-radius: var(--radius-pill);
    transition: background var(--dur-fast) var(--ease-out);
  }
  .shdot:hover, .shdot.open { background: var(--surface-hover); }
  .shdot .dot {
    width: 10px; height: 10px; border-radius: var(--radius-pill);
    background: var(--sh-color, var(--muted));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--sh-color, var(--muted)) 22%, transparent);
  }
  /* Health → colour. ok green, warn amber, down red. */
  .state-ok   { --sh-color: var(--success); }
  .state-warn { --sh-color: var(--warning); }
  .state-down { --sh-color: var(--danger); }
  .state-off  { --sh-color: var(--line); }

  /* A gentle pulse while healthy so it reads as "live", not a static ornament. */
  @media (prefers-reduced-motion: no-preference) {
    .shdot.state-ok .dot { animation: shpulse 2.8s var(--ease-out) infinite; }
  }
  @keyframes shpulse {
    0%, 100% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 22%, transparent); }
    50%      { box-shadow: 0 0 0 5px color-mix(in srgb, var(--success) 10%, transparent); }
  }

  /* Popover — anchored under the dot, right-aligned. Mirrors Drawer's .profmenu. */
  .shpanel {
    position: absolute; top: calc(100% + 6px); right: 0; z-index: var(--z-modal);
    min-width: 248px; max-width: 320px;
    display: flex; flex-direction: column; gap: 2px;
    padding: 8px; background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius-sm); box-shadow: var(--shadow-lg);
  }
  .shhead {
    font-size: var(--text-xs); font-weight: var(--fw-bold); color: var(--muted);
    text-transform: uppercase; letter-spacing: .04em; padding: 2px 4px 6px;
  }
  .shempty { font-size: var(--text-sm); color: var(--muted); padding: 4px; }

  .shrow { display: flex; align-items: flex-start; gap: 9px; padding: 5px 4px; }
  .rowdot {
    flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: var(--radius-pill);
    background: var(--sh-color, var(--muted));
  }
  .shtext { min-width: 0; flex: 1; }
  .shlabel { font-size: var(--text-sm); font-weight: var(--fw-medium); color: var(--text); line-height: 1.3; }
  .shdetail { font-size: var(--text-xs); color: var(--muted); overflow-wrap: anywhere; }

  /* Per-server MCP list, indented under the MCP row. */
  .shservers {
    display: flex; flex-direction: column; gap: 3px;
    margin: 0 4px 4px 21px; padding-left: 8px; border-left: 1px solid var(--line);
  }
  .shsrv { display: flex; align-items: center; gap: 7px; font-size: var(--text-xs); }
  .srvname { flex: none; max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); font-weight: var(--fw-medium); }
  .srvstat { flex: 1; min-width: 0; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .srvstat.bad { color: var(--danger); }
  .recheck {
    flex: none; display: inline-flex; align-items: center; justify-content: center;
    padding: 2px; border: none; background: none; color: var(--muted);
    cursor: pointer; border-radius: 5px; transition: color var(--dur-fast) var(--ease-out);
  }
  .recheck:hover { color: var(--text); }
</style>
