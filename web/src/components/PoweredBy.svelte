<script lang="ts">
  import { m } from '../paraglide/messages.js'
  // The architecture map: which AG2 primitives power this assistant, and
  // (honestly) what's the app layer built on top.
  import { appVersion, ag2Version } from '../store.ts'
  import { route, closeOverlay, replaceOverlay } from '../router.ts'
  import { PRIMITIVES, SUBSYSTEMS, AG2_DOCS, primitiveName } from '../lib/ag2map.ts'
  import Icon from './Icon.svelte'

  // The map is URL-addressed (`#poweredby`), so × / Esc / browser Back all funnel
  // through the one close — Back off the map returns to whatever pushed it (the
  // Settings modal, or the bare page when opened from the Inspector).
  const close = () => closeOverlay()

  // Opened from Settings, the hash carries the Section we came from
  // (`#poweredby=advanced`) — render an explicit way back to it, so returning
  // doesn't depend on the user knowing about browser Back. Absent when the map was
  // opened from the Inspector: there'd be no Settings to go back TO.
  const backTo = $derived($route.overlayValue)
  const back = () => replaceOverlay('settings', backTo)

  const ag2 = PRIMITIVES.filter((p) => p.layer === 'ag2')
  const app = PRIMITIVES.filter((p) => p.layer === 'app')
  // App-layer rows carry no subsystem, and an unknown one keeps the neutral line.
  const dot = (sub: string | undefined) => (sub ? SUBSYSTEMS[sub]?.color : '') || 'var(--line)'

  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
</script>

<svelte:window onkeydown={onKey} />
<!-- Backdrop: click-to-dismiss duplicates the × button, so it stays out of the
     a11y tree rather than becoming a second focusable control. -->
<div class="modal-backdrop" role="presentation" onclick={close}></div>
<div class="modal poweredby">
  <button class="modal-x" aria-label={m.action_close()} onclick={close}>×</button>
  <!-- Window chrome lives in the head: Back at the top-left, × at the top-right
       (absolutely positioned to the same corner band). The back button leads the
       title, the ProfileEditor .pback idiom. -->
  {#if backTo}
    <button class="pbback" onclick={back}><Icon name="chevron-left" size={15} /> {m.settings_title()}</button>
  {/if}
  <h2>{m.pb_title()}</h2>
  <p class="muted">
    {m.pb_lead_pre()} <strong>{m.onboarding_tip_ag2_view()}</strong> {m.pb_lead_mid()}
    <code>&lt;/&gt; AG2</code> {m.pb_lead_post()}
  </p>
  <div class="pbscroll">
    <div class="setsec">{m.pb_primitives()}</div>
    {#each ag2 as p}
      <div class="pbrow">
        <span class="insp-dot" style="background:{dot(p.sub)}"></span>
        <div class="pbtext"><div class="pbname">{primitiveName(p)}</div><div class="pbwhat">{p.what()}</div></div>
      </div>
    {/each}
    <div class="setsec">{m.pb_app_layer()}</div>
    {#each app as p}
      <div class="pbrow">
        <span class="insp-dot" style="background:var(--line)"></span>
        <div class="pbtext"><div class="pbname">{primitiveName(p)}</div><div class="pbwhat">{p.what()}</div></div>
      </div>
    {/each}
  </div>
  <div class="mfoot">
    {#if $appVersion}
      <span class="pbver">AG2 Assistant v{$appVersion}{#if $ag2Version} · AG2 v{$ag2Version}{/if}</span>
    {/if}
    <a class="open" href={AG2_DOCS} target="_blank" rel="noopener noreferrer">{m.pb_docs()} ↗</a>
  </div>
</div>
