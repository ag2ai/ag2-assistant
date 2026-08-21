<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { googleOpen } from '../store.ts'
  import { api } from '../transport/api/index.ts'
  import type { GoogleStatus } from '../schemas/index.ts'
  import { m } from '../paraglide/messages.js'

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
      err = r.error || m.goog_start_failed()
    }
  }
  async function logout() { await api.googleLogout(); refresh() }
  const close = () => ($googleOpen = false)
</script>

<!-- Backdrop: click-to-dismiss duplicates the × button, so it stays out of the
     a11y tree rather than becoming a second focusable control. -->
<div class="modal-backdrop" role="presentation" onclick={close}></div>
<div class="modal">
  <button class="modal-x" aria-label={m.action_close()} onclick={close}>×</button>
  <h2>Google</h2>
  {#if st && st.libs_available === false}
    <!-- Pre-flight: shown in every state, so nobody completes the Google Cloud
         setup only to discover at the first tool call that the libs are absent. -->
    <div class="gwarn">
      <!-- Two whole sentences rather than one with a spliced-in parenthetical: the
           aside sits in a different place in different languages. -->
      <p><b>{m.goog_libs_missing()}</b> {st.signed_in ? m.goog_libs_body_signed_in() : m.goog_libs_body()}</p>
      <p>{m.goog_libs_run()}</p>
      <pre class="ghint">{st.install_hint}</pre>
      <button class="linkbtn" onclick={refresh}>{m.onboarding_cli_recheck()}</button>
    </div>
  {/if}
  {#if !st}
    <p class="muted">{m.loading()}</p>
  {:else if st.signed_in}
    <p>{m.goog_signed_in_pre()} <b>{st.email || m.goog_your_account()}</b>{st.libs_available === false
      ? m.goog_signed_in_tail()
      : m.goog_signed_in_tail_ready()}</p>
    <button class="open" onclick={logout}>{m.integrations_disconnect()}</button>
  {:else if !st.configured}
    <p>{m.goog_byo_pre()} <b>{m.goog_byo_bold()}</b>{m.goog_byo_post()}</p>
    <ol class="gsteps">
      <li>{m.goog_step_console_pre()} <a href="https://console.cloud.google.com" target="_blank" rel="noopener noreferrer">{m.goog_console()}</a>{m.goog_step_console_post()}</li>
      <li>{m.goog_step_consent()}</li>
      <li>{m.goog_step_client()}</li>
      <li>{m.goog_step_paste()}</li>
    </ol>
    <p class="muted" style="font-size:12px">{m.goog_guide()}</p>
    <textarea bind:value={creds} placeholder={'{ "installed": { "client_id": "…", "client_secret": "…", … } }'}></textarea>
    <button class="open" onclick={saveCreds}>{m.goog_save_creds()}</button>
  {:else}
    <p>{connecting ? m.goog_waiting() : m.goog_connect_prompt()}</p>
    {#if err}<p class="muted">{err}</p>{/if}
    <button class="open" onclick={connect}>{m.goog_connect()}</button>
  {/if}
</div>
