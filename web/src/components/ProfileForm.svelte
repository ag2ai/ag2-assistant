<script>
  // Reusable profile form (§5.5): name + palette swatches + workspace. One form,
  // three consumers so they can't drift:
  //   (a) the onboarding multi-profile loop (§5.5) — also the zero-profile bootstrap
  //   (b) Drawer "+" chip create modal (§5.4)
  //   (c) Settings "Profiles" section inline edit of the active profile (§5.4)
  //
  // The form owns only field state + busy/error UI. The actual persistence is the
  // parent's job via onSubmit({name, palette, workspace}) → Promise. If it throws,
  // the message is shown inline (e.g. 400 "palette already in use"). This lets each
  // consumer choose what "submit" means (create, create-then-continue, etc.).
  import { PALETTES } from '../design/palette.js'
  import Icon from './Icon.svelte'

  let {
    // Palette ids already taken by other profiles — hidden from the swatches when
    // creating (plan §5.4/§5.5). `keepPalettes` re-admits ids (e.g. the profile's
    // own palette when editing) even if they're in `claimed`.
    claimed = [],
    keepPalettes = [],
    // Initial values (edit affordances reuse this form shape too).
    initialName = '',
    initialPalette = null,
    initialWorkspace = '',
    submitLabel = 'Create profile',
    busyLabel = 'Creating…',
    // onSubmit({name, palette, workspace}) -> Promise. Throw to show inline error.
    onSubmit,
    autofocus = true,
  } = $props()

  // Available swatches: all palettes minus claimed, plus any explicitly kept.
  const available = $derived(
    PALETTES.filter((p) => !claimed.includes(p.id) || keepPalettes.includes(p.id))
  )

  let name = $state(initialName)
  let palette = $state(initialPalette || (available[0] && available[0].id) || PALETTES[0].id)
  let workspace = $state(initialWorkspace)
  let busy = $state(false)
  let error = $state('')

  // If the currently-selected palette gets claimed out from under us (e.g. the
  // loop removed it), fall back to the first still-available swatch.
  $effect(() => {
    if (!available.some((p) => p.id === palette) && available.length) palette = available[0].id
  })

  const wsPlaceholder = $derived(
    '~/Documents/AG2 Assistant/' + (name.trim() || '<Name>')
  )

  async function submit() {
    if (!name.trim() || busy) return
    busy = true
    error = ''
    try {
      await onSubmit({ name: name.trim(), palette, workspace: workspace.trim() || undefined })
      // On success the parent typically navigates/closes; leave busy true so the
      // button doesn't flash back to idle mid-transition.
    } catch (e) {
      error = (e && e.message) || 'Could not save profile'
      busy = false
    }
  }
</script>

<div class="pf">
  <div class="pf-field">
    <div class="pf-flabel"><span>Profile name</span></div>
    <div class="pf-input">
      <Icon name="message" size={15} />
      <!-- svelte-ignore a11y_autofocus -->
      <input placeholder="e.g. Work" bind:value={name} onkeydown={(e) => e.key === 'Enter' && submit()} autofocus={autofocus} />
    </div>
  </div>

  <div class="pf-field">
    <div class="pf-flabel"><span>Colour</span><span class="hint">its visual identity</span></div>
    <div class="pf-dots">
      {#each available as p (p.id)}
        <button
          class="pf-dot"
          class:on={palette === p.id}
          style="--dot:{p.hex}"
          title={p.label}
          aria-label={p.label}
          type="button"
          onclick={() => (palette = p.id)}
        >
          {#if palette === p.id}<Icon name="check" size={13} />{/if}
        </button>
      {/each}
    </div>
  </div>

  <div class="pf-field">
    <div class="pf-flabel"><span>Workspace folder</span><span class="hint">optional</span></div>
    <div class="pf-input">
      <Icon name="folder" size={15} />
      <input placeholder={wsPlaceholder} bind:value={workspace} onkeydown={(e) => e.key === 'Enter' && submit()} />
    </div>
    <div class="hint">Leave empty to use the default shown above.</div>
  </div>

  {#if error}<div class="pf-error"><Icon name="x" size={13} /> {error}</div>{/if}

  <div class="pf-actions">
    <button class="pf-btn primary" type="button" disabled={!name.trim() || busy} onclick={submit}>
      {busy ? busyLabel : submitLabel} <Icon name="chevron-right" size={16} />
    </button>
  </div>
</div>

<style>
  .pf { display: flex; flex-direction: column; gap: 16px; }

  .pf-field { display: flex; flex-direction: column; gap: 6px; }
  .pf-flabel { display: flex; align-items: baseline; gap: 8px; }
  .pf-flabel > span:first-child { font-size: var(--text-sm); font-weight: var(--fw-semibold); }
  .hint { font-size: var(--text-xs); color: var(--text-muted); }

  .pf-input {
    display: flex; align-items: center; gap: 9px; padding: 0 12px;
    background: var(--surface-sunk); border: 1px solid var(--line);
    border-radius: var(--radius-sm); color: var(--text-muted);
    transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .pf-input:focus-within { border-color: var(--accent); box-shadow: var(--focus-ring); }
  .pf-input input {
    flex: 1; min-width: 0; border: none; background: none; outline: none;
    color: var(--text); font: inherit; font-size: var(--text-sm); padding: 11px 0;
  }

  .pf-dots { display: flex; flex-wrap: wrap; gap: 10px; }
  .pf-dot {
    width: 30px; height: 30px; flex: none; cursor: pointer;
    border-radius: var(--radius-pill); border: 2px solid transparent;
    background: var(--dot); color: #fff;
    display: inline-flex; align-items: center; justify-content: center;
    transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .pf-dot:hover { transform: scale(1.08); }
  .pf-dot.on { box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--dot); }

  .pf-error {
    display: flex; align-items: center; gap: 6px;
    font-size: var(--text-sm); color: var(--danger, #e53c20);
  }

  .pf-actions { display: flex; justify-content: flex-end; margin-top: 4px; }
  .pf-btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 7px; cursor: pointer;
    border: 1px solid var(--accent); border-radius: var(--radius-sm); padding: 10px 18px;
    font: inherit; font-size: var(--text-sm); font-weight: var(--fw-semibold);
    transition: background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
  }
  .pf-btn.primary { background: var(--accent); color: var(--text-on-accent); box-shadow: var(--shadow-accent); }
  .pf-btn.primary:hover { background: var(--accent-hover); }
  .pf-btn.primary:disabled { opacity: .5; cursor: default; box-shadow: none; }
</style>
