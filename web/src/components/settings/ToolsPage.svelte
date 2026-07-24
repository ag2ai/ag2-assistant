<script>
  // Settings → Tools & Permissions: MCP servers + the new install-wide permission grants.
  import McpServers from '../McpServers.svelte'
  import CodingAgents from '../CodingAgents.svelte'
  import PermissionsManager from '../PermissionsManager.svelte'
</script>

<div class="setgroup">MCP servers</div>
<McpServers />

<CodingAgents />

<div class="setsec">Permissions <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<PermissionsManager />
