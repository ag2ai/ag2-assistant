<script lang="ts">
  // Settings → Tools & Permissions: MCP servers + the new install-wide permission grants.
  import McpServers from '../McpServers.svelte'
  import CodingAgents from '../CodingAgents.svelte'
  import PermissionsManager from '../PermissionsManager.svelte'
  import { m } from '../../paraglide/messages.js'
</script>

<div class="setgroup">{m.tools_mcp_servers()}</div>
<McpServers />

<CodingAgents />

<div class="setgroup">{m.tools_permissions()} <span class="setwide" title={m.settings_install_wide_title()}>{m.settings_install_wide()}</span></div>
<PermissionsManager />
