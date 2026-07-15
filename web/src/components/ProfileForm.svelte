<script>
  // Reusable profile form (§5.5): name + palette swatches. One form, three consumers
  // so they can't drift:
  //   (a) the onboarding multi-profile loop (§5.5) — also the zero-profile bootstrap
  //   (b) Drawer "+" chip create modal (§5.4)
  //   (c) Settings "Profiles" section inline edit of the active profile (§5.4)
  //
  // The workspace folder is NOT a user choice — every profile stores its files under
  // the install root, so there's no folder field here.
  //
  // The form owns only field state + busy/error UI. The actual persistence is the
  // parent's job via onSubmit({name, accent}) → Promise. If it throws, the message is
  // shown inline. This lets each consumer choose what "submit" means (create,
  // create-then-continue, etc.). `accent` is an opaque #rrggbb hex (ADR 0002): a
  // preset swatch or any colour from the custom picker.
  import { PALETTES } from '../design/palette.js'
  import Icon from './Icon.svelte'

  let {
    // Preset hexes already taken by other profiles — hidden from the swatches when
    // creating (plan §5.4/§5.5). `keepAccents` re-admits hexes (e.g. the profile's
    // own accent when editing) even if they're in `claimed`. Custom colours are
    // never hidden — this is a gentle nudge over the presets, not a constraint.
    claimed = [],
    keepAccents = [],
    // Initial values (edit affordances reuse this form shape too).
    initialName = '',
    initialAccent = null,
    submitLabel = 'Create profile',
    busyLabel = 'Creating…',
    // onSubmit({name, accent}) -> Promise. Throw to show inline error.
    onSubmit,
    autofocus = true,
  } = $props()

  // Available preset swatches: all presets minus claimed, plus any explicitly kept.
  const available = $derived(
    PALETTES.filter((p) => !claimed.includes(p.hex) || keepAccents.includes(p.hex))
  )

  let name = $state(initialName)
  let accent = $state(initialAccent || (available[0] && available[0].hex) || PALETTES[0].hex)
  let busy = $state(false)
  let error = $state('')

  // Custom = the accent is not one of the (available) presets. Drives the custom
  // swatch's selected state + colour.
  const isCustom = $derived(!PALETTES.some((p) => p.hex === accent))

  // If the selected PRESET gets claimed out from under us (e.g. the loop removed
  // it), fall back to the first still-available swatch. A custom colour is left
  // alone — it's always valid.
  $effect(() => {
    if (!isCustom && !available.some((p) => p.hex === accent) && available.length) {
      accent = available[0].hex
    }
  })

  function pickCustom(e) {
    const v = (e.target.value || '').toLowerCase()
    if (/^#[0-9a-f]{6}$/.test(v)) accent = v
  }

  async function submit() {
    if (!name.trim() || busy) return
    busy = true
    error = ''
    try {
      await onSubmit({ name: name.trim(), accent })
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
          class:on={accent === p.hex}
          style="--dot:{p.hex}"
          title={p.label}
          aria-label={p.label}
          type="button"
          onclick={() => (accent = p.hex)}
        >
          {#if accent === p.hex}<Icon name="check" size={13} />{/if}
        </button>
      {/each}

      <!-- Custom colour: a native <input type=color> hidden behind our swatch.
           Always shows the palette glyph (never the chosen colour) — its job is
           to open the picker; the selected hex reads below in .pf-hex. -->
      <label
        class="pf-dot pf-custom rainbow"
        class:on={isCustom}
        title="Custom colour"
      >
        <Icon name="palette" size={15} />
        <input type="color" value={accent} oninput={pickCustom} aria-label="Custom colour" />
      </label>
    </div>
    <div class="pf-hex">{accent}{#if isCustom} · custom{/if}</div>
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

  /* Custom-colour swatch: wraps a hidden native colour input. Always shows a
     rainbow ring + palette glyph to read as "pick any colour". */
  .pf-custom { position: relative; overflow: hidden; padding: 0; }
  .pf-custom.rainbow {
    background: conic-gradient(from 90deg, #f95339, #ec5d18, #e0b400, #2f8c44, #109e91, #2f6fe0, #7a52ec, #f95339);
  }
  /* The palette glyph sits over the gradient — a drop-shadow keeps it legible. */
  .pf-custom :global(svg) { filter: drop-shadow(0 0 1.5px rgba(0, 0, 0, 0.55)); }
  .pf-custom.rainbow.on { box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent); }
  .pf-custom input {
    position: absolute; inset: 0; width: 100%; height: 100%;
    opacity: 0; cursor: pointer; border: none; padding: 0; background: none;
  }

  .pf-hex {
    margin-top: 2px; font-size: var(--text-xs); color: var(--text-muted);
    font-variant-numeric: tabular-nums; letter-spacing: .02em;
  }

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
