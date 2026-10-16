<script lang="ts">
  // Device appearance and notifications, plus profile preview configuration.
  import { getSettings } from './context.svelte.ts'
  import { soundOnInput, animations, type AnimationQuality } from '../../store.ts'
  import { chime } from '../../lib/chime.ts'
  import Icon from '../Icon.svelte'
  import Appearance from '../Appearance.svelte'
  import { api } from '../../transport/api/index.ts'

  const ctx = getSettings()
  let browser = $state('')
  $effect(() => { browser = ctx.s?.preview_browser || '' })

  // App-wide animation tiers (per-device; see store.animations)
  const FX_MODES: { id: AnimationQuality; label: string; hint: string }[] = [
    { id: 'off', label: 'Off', hint: 'static content' },
    { id: 'basic', label: 'Basic', hint: 'light animation' },
    { id: 'high', label: 'High', hint: 'full 3D scenes' },
  ]
</script>

<div class="setgroup">Appearance</div>
<Appearance />

<div class="setgroup">Animations</div>
<div class="focuspills">
  {#each FX_MODES as m}
    <button class="focuspill" class:on={$animations === m.id} onclick={() => ($animations = m.id)}>
      {m.label} <span style="opacity:.6;font-weight:400">· {m.hint}</span>
    </button>
  {/each}
</div>
<p class="setsub" style="margin:4px 0 0">How animated content (weather panels and more) renders on this device — High drives the GPU; Basic and Off are easy on it.</p>

<div class="setgroup">Notifications</div>
<label class="setcheck">
  <input type="checkbox" bind:checked={$soundOnInput} onchange={(e) => e.currentTarget.checked && chime()} />
  Play a sound when the assistant needs my input
</label>

<div class="setgroup">Visual preview</div>
<p class="setsub">Let the Screen skill inspect rendered Cards and Screens. Choose the absolute path to a Chromium executable on the server. An empty path disables preview for this profile.</p>
<div class="keyrow">
  <input aria-label="Chromium executable" bind:value={browser} placeholder="Absolute path to Chromium or Chrome" disabled={ctx.busy || !ctx.s} />
  <button class="open" disabled={ctx.busy || !ctx.s || browser === ctx.s.preview_browser} onclick={() => ctx.run(() => api.setPreviewBrowser(browser))}>Save</button>
</div>

<div class="setgroup">Re-run setup</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="sparkles" size={15} /> Re-run setup</span>
    <span class="sv">replay the first-run welcome & onboarding</span>
  </div>
  <button class="open" onclick={ctx.reRunSetup}>Open</button>
</div>
