<script>
  // Settings → Integrations (install-wide): messaging channels + Google connect.
  import { getSettings } from './context.svelte.js'
  import Channels from '../Channels.svelte'

  const ctx = getSettings()
</script>

<div class="setsec">Channels <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<Channels />

<div class="setsec">Google <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk">Google</span>
    <span class="sv">{ctx.google == null ? '…' : ctx.google.signed_in ? ('Connected · ' + (ctx.google.email || 'account')) : 'Not connected'}</span>
  </div>
  <button class="open" onclick={ctx.openGoogle}>Manage</button>
</div>
