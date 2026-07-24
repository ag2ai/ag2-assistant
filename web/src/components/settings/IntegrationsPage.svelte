<script>
  // Settings → Integrations (install-wide): messaging channels, Google connect, and
  // the GitHub token (skills registry — not a model provider, so it lives here).
  import { getSettings } from './context.svelte.js'
  import { api } from '../../transport/api.js'
  import Channels from '../Channels.svelte'

  const ctx = getSettings()

  const saveGithub = () => ctx.run(() => api.setKey('github', ctx.drafts.github || '').then(() => { ctx.drafts.github = '' }))
  const clearGithub = () => ctx.run(() => api.setKey('github', ''))
</script>

<div class="setgroup">Channels <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<Channels />

<div class="setgroup">Google <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk">Google</span>
    <span class="sv">{ctx.google == null
      ? '…'
      : ctx.google.signed_in && ctx.google.libs_available === false
        ? 'Needs libraries · not usable'
        : ctx.google.signed_in
          ? ('Connected · ' + (ctx.google.email || 'account'))
          : 'Not connected'}</span>
  </div>
  <button class="open" onclick={ctx.openGoogle}>Manage</button>
</div>

<div class="setgroup">GitHub <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
<p class="setsub">Skills registry — raises the rate limit. Optional.</p>
<div class="keyrow">
  <span class="kp">GitHub</span>
  <input type="password" placeholder={ctx.s.keys.github?.set ? '•••• ' + ctx.s.keys.github.hint : 'paste token'} bind:value={ctx.drafts.github} />
  <button class="open" disabled={ctx.busy} onclick={saveGithub}>Save</button>
  {#if ctx.s.keys.github?.set}<button class="linkbtn" disabled={ctx.busy} onclick={clearGithub}>Clear</button>{/if}
</div>
