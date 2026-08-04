<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { googleOpen } from '../store.ts'
  import { api } from '../transport/api/index.ts'
  import type { GoogleStatus } from '../schemas/index.ts'

  let st: GoogleStatus | null = $state(null)
  let creds = $state('')
  let connecting = $state(false)
  let poll: ReturnType<typeof setInterval> | null = null
  // A sign-in failure is ours, not part of the status the backend reports.
  let err = $state('')

  async function refresh() { try { st = await api.googleStatus(); err = '' } catch {} }
  onMount(refresh)
  onDestroy(() => { if (poll) clearInterval(poll) })

  async function saveCreds() {
    if (!creds.trim()) return
    await api.googleCredentials(creds.trim()); creds = ''; refresh()
  }
  async function connect() {
    const r = await api.googleLoginUrl()
    if (r.ok) {
      window.open(r.auth_url, '_blank')
      connecting = true
      poll = setInterval(async () => {
        const s = await api.googleStatus()
        if (s.signed_in) { if (poll) clearInterval(poll); poll = null; connecting = false; st = s }
      }, 2000)
    } else {
      err = r.error || 'Could not start sign-in'
    }
  }
  async function logout() { await api.googleLogout(); refresh() }
  const close = () => ($googleOpen = false)
</script>

<!-- Backdrop: click-to-dismiss duplicates the × button, so it stays out of the
     a11y tree rather than becoming a second focusable control. -->
<div class="modal-backdrop" role="presentation" onclick={close}></div>
<div class="modal">
  <button class="modal-x" aria-label="Close" onclick={close}>×</button>
  <h2>Google</h2>
  {#if st && st.libs_available === false}
    <!-- Pre-flight: shown in every state, so nobody completes the Google Cloud
         setup only to discover at the first tool call that the libs are absent. -->
    <div class="gwarn">
      <p><b>Optional Google libraries aren't installed.</b> Gmail, Calendar and
      Drive stay unavailable {st.signed_in ? '(even though you\'re signed in) ' : ''}until you add them.</p>
      <p>Run this, then restart AG2 Assistant:</p>
      <pre class="ghint">{st.install_hint}</pre>
      <button class="linkbtn" onclick={refresh}>Re-check</button>
    </div>
  {/if}
  {#if !st}
    <p class="muted">Loading…</p>
  {:else if st.signed_in}
    <p>Signed in as <b>{st.email || 'your account'}</b>.{st.libs_available === false
      ? ''
      : ' AG2 Assistant can use Gmail, Calendar and Drive.'}</p>
    <button class="open" onclick={logout}>Disconnect</button>
  {:else if !st.configured}
    <p>Google integration is <b>bring-your-own</b>: you create a free OAuth client in
    your own Google Cloud, and credentials stay on this machine.</p>
    <ol class="gsteps">
      <li>In <a href="https://console.cloud.google.com" target="_blank" rel="noopener noreferrer">Google Cloud Console</a>, create a project and <b>enable</b> the Gmail, Google Calendar and Google Drive APIs.</li>
      <li>OAuth consent screen: pick <b>Internal</b> on a Workspace account (best — no warnings, no token expiry), or <b>External</b> + add yourself as a test user on a personal account (re-consent every 7 days).</li>
      <li>Credentials → Create OAuth client ID → <b>Desktop app</b> → <b>Download JSON</b> on the confirmation dialog (it's only offered at creation).</li>
      <li>Paste the JSON file's contents below.</li>
    </ol>
    <p class="muted" style="font-size:12px">Full guide: docs/usage.md → “Google (Gmail / Calendar / Drive)”.</p>
    <textarea bind:value={creds} placeholder={'{ "installed": { "client_id": "…", "client_secret": "…", … } }'}></textarea>
    <button class="open" onclick={saveCreds}>Save credentials</button>
  {:else}
    <p>{connecting ? 'Waiting for Google — complete consent in the opened tab…' : 'Connect your Google account.'}</p>
    {#if err}<p class="muted">{err}</p>{/if}
    <button class="open" onclick={connect}>Connect Google</button>
  {/if}
</div>
