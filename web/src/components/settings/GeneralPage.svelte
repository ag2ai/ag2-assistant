<script lang="ts">
  // Settings → General (per-device): Appearance, Language, Animations, Notifications, Re-run setup.
  import { getSettings } from './context.svelte.ts'
  import { soundOnInput, animations, type AnimationQuality } from '../../store.ts'
  import { chime } from '../../lib/chime.ts'
  import { uiLocale, setUiLocale } from '../../lib/i18n.ts'
  import { UI_LOCALES, LOCALE_ENDONYM } from '../../lib/locale.ts'
  import { m } from '../../paraglide/messages.js'
  import Icon from '../Icon.svelte'
  import Appearance from '../Appearance.svelte'

  const ctx = getSettings()

  // App-wide animation tiers (per-device; see store.animations)
  const FX_MODES: { id: AnimationQuality; label: string; hint: string }[] = [
    { id: 'off', label: m.fx_off(), hint: m.fx_off_hint() },
    { id: 'basic', label: m.fx_basic(), hint: m.fx_basic_hint() },
    { id: 'high', label: m.fx_high(), hint: m.fx_high_hint() },
  ]
</script>

<div class="setgroup">{m.settings_appearance()}</div>
<Appearance />

<div class="setgroup">{m.settings_language()}</div>
<div class="focuspills">
  {#each UI_LOCALES as locale}
    <button class="focuspill" class:on={$uiLocale === locale} lang={locale} onclick={() => setUiLocale(locale)}>
      {LOCALE_ENDONYM[locale]}
    </button>
  {/each}
</div>
<p class="setsub" style="margin:4px 0 0">{m.settings_language_hint()}</p>

<div class="setgroup">{m.settings_animations()}</div>
<div class="focuspills">
  {#each FX_MODES as fx}
    <button class="focuspill" class:on={$animations === fx.id} onclick={() => ($animations = fx.id)}>
      {fx.label} <span style="opacity:.6;font-weight:400">· {fx.hint}</span>
    </button>
  {/each}
</div>
<p class="setsub" style="margin:4px 0 0">{m.settings_animations_hint()}</p>

<div class="setgroup">{m.settings_notifications()}</div>
<label class="setcheck">
  <input type="checkbox" bind:checked={$soundOnInput} onchange={(e) => e.currentTarget.checked && chime()} />
  {m.notifications_sound()}
</label>

<div class="setgroup">{m.settings_rerun()}</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="sparkles" size={15} /> {m.rerun_label()}</span>
    <span class="sv">{m.rerun_hint()}</span>
  </div>
  <button class="open" onclick={ctx.reRunSetup}>{m.rerun_open()}</button>
</div>
