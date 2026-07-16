<script>
  // Settings → Tools & Permissions: MCP servers + the new install-wide permission grants.
  import { getSettings } from './context.svelte.js'
  import McpServers from '../McpServers.svelte'
  import CodingAgents from '../CodingAgents.svelte'
  import PermissionsManager from '../PermissionsManager.svelte'

  const ctx = getSettings()
</script>

<div class="setsec">MCP servers</div>
<McpServers />

<CodingAgents />

<div class="setsec">Permissions <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<PermissionsManager roots={ctx.s.fs || {}} />
