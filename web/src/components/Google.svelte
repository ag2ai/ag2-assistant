<script>
  import { onMount, onDestroy } from 'svelte'
  import { googleOpen } from '../store.js'
  import { api } from '../transport/api.js'

  let st = $state(null)
  let creds = $state('')
  let connecting = $state(false)
  let poll = null

  async function refresh() { try { st = await api.googleStatus() } catch {} }
  onMount(refresh)
  onDestroy(() => { if (poll) clearInterval(poll) })

  async function saveCreds() {
    if (!creds.trim()) return
    await api.googleCredentials(creds.trim()); creds = ''; refresh()
  }
  async function connect() {
    const r = await api.googleLoginUrl()
    if (r.ok && r.auth_url) {
      window.open(r.auth_url, '_blank')
      connecting = true
      poll = setInterval(async () => {
        const s = await api.googleStatus()
        if (s.signed_in) { clearInterval(poll); poll = null; connecting = false; st = s }
      }, 2000)
    } else {
      st = { ...st, error: r.error || 'Could not start sign-in' }
    }
  }
  async function logout() { await api.googleLogout(); refresh() }
  const close = () => ($googleOpen = false)
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal">
  <h2>Google</h2>
  {#if !st}
    <p class="muted">Loading…</p>
  {:else if st.signed_in}
    <p>Connected as <b>{st.email || 'your account'}</b>. AG2 Assistant can use Gmail, Calendar and Drive.</p>
    <button class="open" onclick={logout}>Disconnect</button>
  {:else if !st.configured}
    <p>Paste your Google OAuth <b>client</b> JSON to enable Google integration.</p>
    <textarea bind:value={creds} placeholder={'{ "web": { "client_id": "…", "client_secret": "…", … } }'}></textarea>
    <button class="open" onclick={saveCreds}>Save credentials</button>
  {:else}
    <p>{connecting ? 'Waiting for Google — complete consent in the opened tab…' : 'Connect your Google account.'}</p>
    {#if st.error}<p class="muted">{st.error}</p>{/if}
    <button class="open" onclick={connect}>Connect Google</button>
  {/if}
  <button class="modal-close" onclick={close}>Close</button>
</div>
