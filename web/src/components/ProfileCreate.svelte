<script>
  // Zero-profile bootstrap (WP6 §3.5 / §7 Phase 1 item 5). Shown when the server
  // reports no profiles yet. Bare-bones by design: name, palette dot picker (the
  // six design-system palettes), optional workspace. On submit it POSTs to
  // /api/profiles (which creates AND boots the runtime), marks onboarding done
  // (in Phase 1 this form IS the whole onboarding flow — see plan §7), then hands
  // the new pid back to the boot flow via onCreated. Phase 2 grows this into the
  // full multi-step onboarding loop + "+" chip modal.
  import { PALETTES } from '../design/palette.js'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'
  import ag2Logo from '../assets/ag2.svg'
  import ag2LogoWhite from '../assets/ag2-white.svg'

  // onCreated(profile) — parent (App.svelte) persists the id + boots.
  let { onCreated } = $props()

  let name = $state('')
  let palette = $state(PALETTES[0].id)
  let workspace = $state('')
  let busy = $state(false)
  let error = $state('')

  const wsPlaceholder = $derived(
    '~/Documents/AG2 Assistant/' + (name.trim() || '<Name>')
  )

  async function submit() {
    if (!name.trim() || busy) return
    busy = true
    error = ''
    try {
      const res = await api.createProfile(name.trim(), palette, workspace.trim() || undefined)
      // In Phase 1 this form is the entire onboarding flow, so finish it here —
      // otherwise the install sits at onboarded:false forever (plan §7).
      try { await api.setOnboarded() } catch {}
      onCreated(res.profile)
    } catch (e) {
      error = (e && e.message) || 'Could not create profile'
      busy = false
    }
  }
</script>

<div class="pc">
  <div class="pc-card ag2-rise">
    <div class="pc-brand">
      <img class="brandlogo on-light" src={ag2Logo} alt="AG2" />
      <img class="brandlogo on-dark" src={ag2LogoWhite} alt="AG2" />
      <span class="pc-brandname">Assistant</span>
    </div>

    <h1>Create your first profile</h1>
    <p class="lead">A profile is a colour-coded, isolated workspace — its own chats, tasks, memory, and files. You can add more later.</p>

    <div class="pc-field">
      <div class="pc-flabel"><span>Profile name</span></div>
      <div class="pc-input">
        <Icon name="message" size={15} />
        <input placeholder="e.g. Work" bind:value={name} onkeydown={(e) => e.key === 'Enter' && submit()} autofocus />
      </div>
    </div>

    <div class="pc-field">
      <div class="pc-flabel"><span>Colour</span><span class="hint">its visual identity</span></div>
      <div class="pc-dots">
        {#each PALETTES as p}
          <button
            class="pc-dot"
            class:on={palette === p.id}
            style="--dot:{p.hex}"
            title={p.label}
            aria-label={p.label}
            onclick={() => (palette = p.id)}
          >
            {#if palette === p.id}<Icon name="check" size={13} />{/if}
          </button>
        {/each}
      </div>
    </div>

    <div class="pc-field">
      <div class="pc-flabel"><span>Workspace folder</span><span class="hint">optional</span></div>
      <div class="pc-input">
        <Icon name="folder" size={15} />
        <input placeholder={wsPlaceholder} bind:value={workspace} onkeydown={(e) => e.key === 'Enter' && submit()} />
      </div>
      <div class="hint">Leave empty to use the default shown above.</div>
    </div>

    {#if error}<div class="pc-error"><Icon name="x" size={13} /> {error}</div>{/if}

    <div class="pc-actions">
      <button class="pc-btn primary" disabled={!name.trim() || busy} onclick={submit}>
        {busy ? 'Creating…' : 'Create profile'} <Icon name="chevron-right" size={16} />
      </button>
    </div>
  </div>
</div>

<style>
  .pc {
    position: fixed; inset: 0; z-index: 100; display: flex;
    align-items: center; justify-content: center; padding: 24px;
    background: var(--bg); color: var(--text);
    font-family: var(--font-sans); animation: ag2-fade var(--dur-base) var(--ease-out) both;
  }
  .pc-card {
    width: 100%; max-width: 460px; display: flex; flex-direction: column; gap: 16px;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius-lg); padding: 32px;
  }
  .pc-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .pc-brandname { font-family: var(--font-display); font-weight: var(--fw-bold); font-size: var(--text-lg); }
  .pc-card h1 { font-size: var(--text-2xl); }
  .lead { font-size: var(--text-sm); color: var(--text-muted); line-height: var(--leading-normal); margin: -8px 0 4px; }

  .pc-field { display: flex; flex-direction: column; gap: 6px; }
  .pc-flabel { display: flex; align-items: baseline; gap: 8px; }
  .pc-flabel > span:first-child { font-size: var(--text-sm); font-weight: var(--fw-semibold); }
  .hint { font-size: var(--text-xs); color: var(--text-muted); }

  .pc-input {
    display: flex; align-items: center; gap: 9px; padding: 0 12px;
    background: var(--surface-sunk); border: 1px solid var(--line);
    border-radius: var(--radius-sm); color: var(--text-muted);
    transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .pc-input:focus-within { border-color: var(--accent); box-shadow: var(--focus-ring); }
  .pc-input input {
    flex: 1; min-width: 0; border: none; background: none; outline: none;
    color: var(--text); font: inherit; font-size: var(--text-sm); padding: 11px 0;
  }

  .pc-dots { display: flex; flex-wrap: wrap; gap: 10px; }
  .pc-dot {
    width: 30px; height: 30px; flex: none; cursor: pointer;
    border-radius: var(--radius-pill); border: 2px solid transparent;
    background: var(--dot); color: #fff;
    display: inline-flex; align-items: center; justify-content: center;
    transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .pc-dot:hover { transform: scale(1.08); }
  .pc-dot.on { box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--dot); }

  .pc-error {
    display: flex; align-items: center; gap: 6px;
    font-size: var(--text-sm); color: var(--danger, #e53c20);
  }

  .pc-actions { display: flex; justify-content: flex-end; margin-top: 4px; }
  .pc-btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 7px; cursor: pointer;
    border: 1px solid var(--accent); border-radius: var(--radius-sm); padding: 10px 18px;
    font: inherit; font-size: var(--text-sm); font-weight: var(--fw-semibold);
    transition: background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
  }
  .pc-btn.primary { background: var(--accent); color: var(--text-on-accent); box-shadow: var(--shadow-accent); }
  .pc-btn.primary:hover { background: var(--accent-hover); }
  .pc-btn.primary:disabled { opacity: .5; cursor: default; box-shadow: none; }
</style>
