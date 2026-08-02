<script>
  // Settings → Tools & Permissions → "Permissions". Persistent, install-wide grants
  // the assistant honours without re-prompting. One group:
  //   • Command grants — rule strings: a bare tool name ("gmail_send") allows every call
  //     to that ACTION tool; "tool(prefix *)" allows shell commands whose first token
  //     matches. Arbitrary-execution tools (shell without a prefix, run_code) can never
  //     be blanket-granted — the server rejects those rules.
  // Folder access is no longer here — it lives in Settings → Folders (the install-wide
  // Folder registry + Grants, ADR 0006).
  // Self-contained like McpServers.svelte: owns its list state; every mutator replaces
  // `perms` wholesale from the endpoint's full-snapshot response ({ok, commands}) —
  // no follow-up GET needed.
  import { onMount } from 'svelte'
  import { api } from '../transport/api/index.ts'
  import Icon from './Icon.svelte'

  let perms = $state({ commands: [] })
  let busy = $state(false)
  let err = $state('')

  // Command-rule text input.
  let cmd = $state('')

  // Split "run_code" or "run_shell_command(git *)" into {tool, prefix} — the same
  // shape as the backend parse_command_rule. The server re-validates and builds the
  // canonical rule string, so this is only for the dedupe guard + the POST body.
  const RULE_RE = /^([\w.-]+)\(([^()\s][^()]*) \*\)$/
  function splitRule(raw) {
    const v = String(raw || '').trim()
    if (!v) return null
    const m = v.match(RULE_RE)
    if (m) return { tool: m[1], prefix: m[2] }
    if (/^[\w.-]+$/.test(v)) return { tool: v, prefix: null }
    return null
  }
  const ruleOf = (p) => (p.prefix ? `${p.tool}(${p.prefix} *)` : p.tool)
  // Add is enabled only for a well-formed rule that isn't already granted (dedupe).
  const cmdReady = $derived.by(() => {
    const p = splitRule(cmd)
    return !!p && !perms.commands.includes(ruleOf(p))
  })

  const apply = (r) => {
    perms = { commands: r.commands || [] }
  }

  onMount(async () => {
    try { apply(await api.permissions()) } catch (e) { err = String(e.message || e) }
  })

  // Run a mutator, replace perms from its full-snapshot response.
  async function run(fn) {
    err = ''; busy = true
    try { apply(await fn()) } catch (e) { err = String(e.message || e) }
    busy = false
  }

  const revokeCommand = (rule) => run(() => api.revokeCommand(rule))
  function addCommand() {
    const p = splitRule(cmd)
    if (!p || !cmdReady) return
    run(() => api.grantCommand(p.tool, p.prefix).then((r) => { cmd = ''; return r }))
  }
</script>

{#if err}<p class="muted permerr">{err}</p>{/if}

<!-- Command grants -->
<div class="permgroup">
  <div class="permhd">Command grants</div>
  {#if !perms.commands.length}<p class="muted permempty">No command grants yet.</p>{/if}
  {#each perms.commands as rule (rule)}
    <div class="permrow">
      <span class="permico"><Icon name="code" size={14} /></span>
      <span class="permval" title={rule}>{rule}</span>
      <button class="linkbtn danger" disabled={busy} onclick={() => revokeCommand(rule)}>Remove</button>
    </div>
  {/each}
  <div class="keyrow">
    <input
      type="text"
      placeholder="gmail_send or run_shell_command(git *)"
      bind:value={cmd}
      onkeydown={(e) => e.key === 'Enter' && addCommand()}
    />
    <button class="open" disabled={busy || !cmdReady} onclick={addCommand}>Add</button>
  </div>
  <p class="muted permhint">A tool name allows every call to that tool (action tools like gmail_send). Shell takes a prefix rule — run_shell_command(git *) — and host code runs (run_code) are approved individually, never blanket-allowed.</p>
</div>

<style>
  .permgroup { display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; }
  .permhd { font-size: var(--text-xs); font-weight: var(--fw-semibold); color: var(--text-muted); }
  .permrow {
    display: flex; align-items: center; gap: 8px;
    border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 7px 10px;
  }
  .permico { flex: none; display: inline-flex; color: var(--text-muted); }
  .permval {
    flex: 1; min-width: 0; font-family: var(--font-mono); font-size: 12px; color: var(--text);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .permempty { font-size: 13px; margin: 0; }
  .permhint { font-size: 12px; margin: 2px 0 0; }
  .permerr { color: var(--danger); font-size: 13px; margin: 0; }
</style>
