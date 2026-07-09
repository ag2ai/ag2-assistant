<script>
  // Settings → Tools & Permissions → "Permissions". Persistent, install-wide grants
  // the assistant honours without re-prompting. Three groups:
  //   • Granted folders — folders the assistant may act in
  //   • Blocked folders — folders explicitly off-limits (a block wins over a grant)
  //   • Command grants — rule strings: a bare tool name ("gmail_send") allows every call
  //     to that ACTION tool; "tool(prefix *)" allows shell commands whose first token
  //     matches. Arbitrary-execution tools (shell without a prefix, run_code) can never
  //     be blanket-granted — the server rejects those rules.
  // Self-contained like McpServers.svelte: owns its list state; every mutator replaces
  // `perms` wholesale from the endpoint's full-snapshot response ({ok, folders, blocked,
  // commands}) — no follow-up GET needed. `roots` (the fs browser roots) is passed in.
  import { onMount } from 'svelte'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'
  import FolderPicker from './FolderPicker.svelte'

  let { roots = {} } = $props()

  let perms = $state({ folders: [], blocked: [], commands: [] })
  let busy = $state(false)
  let err = $state('')

  // Collapsible folder pickers (the Project-folder collapse pattern).
  let addGrant = $state(false)
  let addBlock = $state(false)

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
    perms = { folders: r.folders || [], blocked: r.blocked || [], commands: r.commands || [] }
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

  const grantFolder = (path) => run(() => api.grantFolder(path).then((r) => { addGrant = false; return r }))
  const revokeFolder = (path) => run(() => api.revokeFolder(path))
  const blockFolder = (path) => run(() => api.blockFolder(path).then((r) => { addBlock = false; return r }))
  const unblockFolder = (path) => run(() => api.unblockFolder(path))
  const revokeCommand = (rule) => run(() => api.revokeCommand(rule))
  function addCommand() {
    const p = splitRule(cmd)
    if (!p || !cmdReady) return
    run(() => api.grantCommand(p.tool, p.prefix).then((r) => { cmd = ''; return r }))
  }
</script>

{#if err}<p class="muted permerr">{err}</p>{/if}

<!-- Granted folders -->
<div class="permgroup">
  <div class="permhd">Granted folders</div>
  {#if !perms.folders.length}<p class="muted permempty">No folders granted yet.</p>{/if}
  {#each perms.folders as path (path)}
    <div class="permrow">
      <span class="permico"><Icon name="folder" size={14} /></span>
      <span class="permval" title={path}>{path}</span>
      <button class="linkbtn" disabled={busy} onclick={() => revokeFolder(path)}>Remove</button>
    </div>
  {/each}
  {#if !addGrant}
    <button class="open permadd" onclick={() => (addGrant = true)}>Grant a folder…</button>
  {:else}
    <FolderPicker {roots} start={roots.cwd} {busy} onUse={grantFolder} />
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (addGrant = false)}>Cancel</button>
    </div>
  {/if}
</div>

<!-- Blocked folders -->
<div class="permgroup">
  <div class="permhd">Blocked folders</div>
  {#if !perms.blocked.length}<p class="muted permempty">No folders blocked.</p>{/if}
  {#each perms.blocked as path (path)}
    <div class="permrow">
      <span class="permico"><Icon name="folder" size={14} /></span>
      <span class="permval" title={path}>{path}</span>
      <button class="linkbtn" disabled={busy} onclick={() => unblockFolder(path)}>Remove</button>
    </div>
  {/each}
  {#if !addBlock}
    <button class="open permadd" onclick={() => (addBlock = true)}>Block a folder…</button>
  {:else}
    <FolderPicker {roots} start={roots.cwd} {busy} onUse={blockFolder} />
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (addBlock = false)}>Cancel</button>
    </div>
  {/if}
</div>

<!-- Command grants -->
<div class="permgroup">
  <div class="permhd">Command grants</div>
  {#if !perms.commands.length}<p class="muted permempty">No command grants yet.</p>{/if}
  {#each perms.commands as rule (rule)}
    <div class="permrow">
      <span class="permico"><Icon name="code" size={14} /></span>
      <span class="permval" title={rule}>{rule}</span>
      <button class="linkbtn" disabled={busy} onclick={() => revokeCommand(rule)}>Remove</button>
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
  .permadd { align-self: flex-start; }
  .permhint { font-size: 12px; margin: 2px 0 0; }
  .permerr { color: #d8552f; font-size: 13px; margin: 0; }
</style>
