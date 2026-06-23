<script>
  // First-run welcome: a warm four-step flow that collects a name, provider keys +
  // model, focus areas, and appearance — then seeds the same store Settings reads
  // so the two stay in sync. Shown on first launch when no key is stored, or via
  // Settings → "Re-run setup".
  import { onboardingOpen, profile } from '../store.js'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'
  import Appearance from './Appearance.svelte'
  import ag2Mark from '../assets/ag2-mark.svg'
  import ag2MarkWhite from '../assets/ag2-mark-white.svg'

  const STEPS = ['Welcome', 'Connect', 'Personalize', 'Ready']
  const FEATURES = [
    { icon: 'zap', title: 'Powered by AG2 Beta', desc: 'A universal agent runtime — the open-source framework behind every reply.' },
    { icon: 'globe', title: 'Acts, not just answers', desc: 'Searches the web, runs code, generates images, and manages scheduled tasks.' },
    { icon: 'brain', title: 'Remembers what matters', desc: 'Builds a private memory of your preferences so it gets more helpful over time.' },
  ]
  const MODELS = [
    { label: 'Gemini · gemini-3.5-flash', provider: 'gemini', model: 'gemini-3.5-flash' },
    { label: 'OpenAI · gpt-5', provider: 'openai', model: 'gpt-5' },
    { label: 'Anthropic · claude-opus-4', provider: 'anthropic', model: 'claude-opus-4' },
  ]
  const FOCUS = [
    { id: 'research', label: 'Research', icon: 'search' },
    { id: 'coding', label: 'Coding', icon: 'code' },
    { id: 'scheduling', label: 'Scheduling', icon: 'clock' },
    { id: 'writing', label: 'Writing', icon: 'file-text' },
    { id: 'data', label: 'Data & reports', icon: 'list' },
    { id: 'images', label: 'Images', icon: 'image' },
  ]
  const KEY_FIELDS = [
    { id: 'gemini', label: 'Gemini', hint: 'recommended' },
    { id: 'openai', label: 'OpenAI', hint: 'optional' },
    { id: 'anthropic', label: 'Anthropic', hint: 'optional' },
  ]

  let step = $state(0)
  let keys = $state({ gemini: '', openai: '', anthropic: '' })
  let modelLabel = $state(MODELS[0].label)
  let name = $state($profile.name || '')
  // Pre-select the user's saved focuses, or sensible defaults on a truly fresh run.
  let focuses = $state($profile.focuses?.length ? [...$profile.focuses] : ['research', 'coding'])
  let busy = $state(false)

  const hasKey = $derived(!!(keys.gemini.trim() || keys.openai.trim() || keys.anthropic.trim()))
  const canNext = $derived(step !== 1 || hasKey)
  const toggleFocus = (id) =>
    (focuses = focuses.includes(id) ? focuses.filter((x) => x !== id) : [...focuses, id])

  const next = () => (step = Math.min(step + 1, STEPS.length - 1))
  const back = () => (step = Math.max(step - 1, 0))
  const startFromWelcome = () => { if (name.trim() || true) next() }

  // Skip into the app without keys (degraded). Still records the name/focuses chosen.
  function skip() {
    $profile = { name: name.trim(), focuses }
    api.setOnboarded().catch(() => {}) // mark done per install (server-side)
    $onboardingOpen = false
  }

  // Persist everything into the same store Settings reads, then enter the app.
  async function finish() {
    busy = true
    try {
      for (const [prov, val] of Object.entries(keys)) {
        if (val.trim()) { try { await api.setKey(prov, val.trim()) } catch {} }
      }
      const m = MODELS.find((x) => x.label === modelLabel)
      if (m) { try { await api.setLlm(m.provider, m.model) } catch {} }
      try { await api.setOnboarded() } catch {} // mark done per install (server-side)
    } finally {
      $profile = { name: name.trim(), focuses }
      $onboardingOpen = false
      busy = false
    }
  }
</script>

<div class="onb">
  <!-- Hero panel -->
  <aside class="onb-hero">
    <div class="onb-brand">
      <img class="brandlogo on-light" src={ag2Mark} alt="AG2" />
      <img class="brandlogo on-dark" src={ag2MarkWhite} alt="AG2" />
      <span class="onb-brandname">Assistant</span>
    </div>
    <div class="onb-herobody">
      <h1>Welcome.<br />Let's get you set up.</h1>
      <p>A friendly AI assistant that actually does the work — and shows you exactly how, in the open.</p>
    </div>
    <div class="onb-features">
      {#each FEATURES as f, i}
        <div class="onb-feature ag2-rise" style="--i:{i}">
          <span class="onb-featicon"><Icon name={f.icon} size={17} /></span>
          <div>
            <div class="onb-feattitle">{f.title}</div>
            <div class="onb-featdesc">{f.desc}</div>
          </div>
        </div>
      {/each}
    </div>
  </aside>

  <!-- Form column -->
  <main class="onb-main">
    <div class="onb-stepper">
      {#each STEPS as s, i}
        <div class="onb-stepitem" class:active={i === step}>
          <span class="onb-stepnum" class:done={i < step} class:on={i <= step}>
            {#if i < step}<Icon name="check" size={12} />{:else}{i + 1}{/if}
          </span>
          <span class="onb-steplabel">{s}</span>
        </div>
        {#if i < STEPS.length - 1}<span class="onb-stepline"></span>{/if}
      {/each}
    </div>

    <div class="onb-body" class:center={step === 0}>
      {#key step}
        <div class="ag2-rise onb-step">
          {#if step === 0}
            <h2 class="big">Hello — ready when you are.</h2>
            <p class="lead">Setup takes about a minute. You'll connect a model, pick a few preferences, and you're off. Nothing leaves your machine — keys and settings stay local.</p>
            <div class="onb-field">
              <div class="onb-flabel"><span>First, what should I call you?</span><span class="hint">so I can greet you properly</span></div>
              <div class="onb-input">
                <Icon name="message" size={15} />
                <input placeholder="Your name" bind:value={name} onkeydown={(e) => e.key === 'Enter' && startFromWelcome()} />
              </div>
            </div>
            <div class="onb-welcomeactions">
              <button class="onb-btn primary lg" onclick={startFromWelcome}>Get started <Icon name="chevron-right" size={17} /></button>
              <button class="onb-btn ghost lg" onclick={skip}>I'll explore first</button>
            </div>

          {:else if step === 1}
            <h2>Connect a model</h2>
            <p class="lead">Add at least one provider key. It's stored locally and used to power your assistant — you can change these anytime in Settings.</p>
            {#each KEY_FIELDS as k}
              <div class="onb-field">
                <div class="onb-flabel"><span>{k.label} API key</span><span class="hint">{k.hint}</span></div>
                <div class="onb-input">
                  <Icon name="settings" size={15} />
                  <input type="password" placeholder="paste key…" bind:value={keys[k.id]} />
                </div>
              </div>
            {/each}
            <div class="onb-field">
              <div class="onb-flabel"><span>Assistant model</span></div>
              <div class="onb-pills">
                {#each MODELS as m}
                  <button class="onb-pill" class:on={modelLabel === m.label} onclick={() => (modelLabel = m.label)}>{m.label}</button>
                {/each}
              </div>
            </div>

          {:else if step === 2}
            <h2>{name.trim() ? `Make it yours, ${name.trim()}` : 'Make it yours'}</h2>
            <p class="lead">A couple of touches so the assistant feels like home.</p>
            <div class="onb-field">
              <div class="onb-flabel"><span>What can I help with?</span><span class="hint">pick any that fit</span></div>
              <div class="onb-pills">
                {#each FOCUS as f}
                  <button class="onb-pill" class:on={focuses.includes(f.id)} onclick={() => toggleFocus(f.id)}>
                    <Icon name={f.icon} size={14} /> {f.label}
                  </button>
                {/each}
              </div>
            </div>
            <div class="onb-field">
              <div class="onb-flabel"><span>Appearance</span></div>
              <Appearance />
            </div>

          {:else}
            <div class="onb-readyhead">
              <span class="onb-readytick ag2-glow"><Icon name="check" size={26} /></span>
              <div>
                <h2>{name.trim() ? `You're all set, ${name.trim()}.` : "You're all set."}</h2>
                <p class="lead">Your preferences are saved to Settings. Let's get to work.</p>
              </div>
            </div>
            <div class="onb-summary">
              <div class="onb-sumrow"><span class="onb-sumicon"><Icon name="cpu" size={16} /></span><span class="onb-sumkey">Model</span><span class="onb-sumval">{modelLabel}</span></div>
              <div class="onb-sumrow"><span class="onb-sumicon"><Icon name="sparkles" size={16} /></span><span class="onb-sumkey">Focus</span><span class="onb-sumval cap">{focuses.length ? focuses.join(', ') : 'Anything you need'}</span></div>
            </div>
            <div class="onb-tip">
              <Icon name="zap" size={14} /><span>Every action runs on the AG2 Stream — toggle <b>AG2 view</b> anytime to watch it live.</span>
            </div>
          {/if}
        </div>
      {/key}
    </div>

    {#if step > 0}
      <div class="onb-nav">
        <button class="onb-btn ghost" onclick={back}><Icon name="chevron-left" size={16} /> Back</button>
        <div class="onb-navright">
          {#if step === 1 && !hasKey}<span class="hint">Add a key to continue</span>{/if}
          {#if step < STEPS.length - 1}
            <button class="onb-btn primary" disabled={!canNext} onclick={next}>Continue <Icon name="chevron-right" size={16} /></button>
          {:else}
            <button class="onb-btn primary" disabled={busy} onclick={finish}>Start using AG2 Assistant <Icon name="send" size={15} /></button>
          {/if}
        </div>
      </div>
    {/if}
  </main>
</div>

<style>
  .onb {
    position: fixed; inset: 0; z-index: 100; display: flex;
    background: var(--bg); color: var(--text);
    font-family: var(--font-sans); animation: ag2-fade var(--dur-base) var(--ease-out) both;
  }

  /* Hero */
  .onb-hero {
    width: 42%; flex: none; display: flex; flex-direction: column;
    padding: 48px 44px; background: var(--accent-soft);
    border-right: 1px solid var(--line); overflow: hidden;
  }
  .onb-brand { display: flex; align-items: center; gap: 12px; }
  .onb-brandname { font-family: var(--font-display); font-weight: var(--fw-bold); font-size: var(--text-2xl); }
  .onb-herobody { margin-top: 40px; }
  .onb-herobody h1 { font-size: var(--text-4xl); line-height: var(--leading-tight); }
  .onb-herobody p { margin-top: 14px; font-size: var(--text-md); color: var(--text-muted); line-height: var(--leading-normal); max-width: 34ch; }
  .onb-features { margin-top: auto; display: flex; flex-direction: column; gap: 18px; }
  .onb-feature { display: flex; gap: 12px; align-items: flex-start; }
  .onb-featicon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; flex: none; border-radius: var(--radius-sm);
    background: var(--surface); border: 1px solid var(--line); color: var(--accent);
  }
  .onb-feattitle { font-size: var(--text-sm); font-weight: var(--fw-bold); }
  .onb-featdesc { font-size: var(--text-sm); color: var(--text-muted); line-height: var(--leading-snug); }

  /* Main column */
  .onb-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .onb-stepper { display: flex; align-items: center; gap: 8px; padding: 26px 44px 0; flex-wrap: wrap; }
  .onb-stepitem { display: flex; align-items: center; gap: 7px; }
  .onb-stepnum {
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; flex: none; border-radius: var(--radius-pill);
    font-size: var(--text-xs); font-weight: var(--fw-bold);
    background: var(--surface-sunk); color: var(--text-faint);
    border: 1px solid var(--line); transition: all var(--dur-base) var(--ease-out);
  }
  .onb-stepnum.on { background: var(--accent); color: var(--text-on-accent); border-color: var(--accent); }
  .onb-steplabel { font-size: var(--text-xs); font-weight: var(--fw-semibold); color: var(--text-muted); }
  .onb-stepitem.active .onb-steplabel { color: var(--text); }
  .onb-stepline { width: 18px; height: 1px; background: var(--line); }

  .onb-body { flex: 1; overflow-y: auto; padding: 32px 44px; display: flex; flex-direction: column; }
  .onb-body.center { justify-content: center; }
  .onb-step { display: flex; flex-direction: column; gap: 16px; max-width: 52ch; }
  .onb-step h2 { font-size: var(--text-2xl); }
  .onb-step h2.big { font-size: var(--text-3xl); }
  .onb-step .lead { font-size: var(--text-md); color: var(--text-muted); line-height: var(--leading-normal); margin: -6px 0 0; }

  .onb-field { display: flex; flex-direction: column; gap: 6px; }
  .onb-flabel { display: flex; align-items: baseline; gap: 8px; }
  .onb-flabel > span:first-child { font-size: var(--text-sm); font-weight: var(--fw-semibold); }
  .onb-flabel .hint { font-size: var(--text-xs); color: var(--text-muted); }
  .hint { font-size: var(--text-xs); color: var(--text-muted); }

  .onb-input {
    display: flex; align-items: center; gap: 9px; padding: 0 12px;
    background: var(--surface-sunk); border: 1px solid var(--line);
    border-radius: var(--radius-sm); color: var(--text-muted);
    transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .onb-input:focus-within { border-color: var(--accent); box-shadow: var(--focus-ring); }
  .onb-input input {
    flex: 1; min-width: 0; border: none; background: none; outline: none;
    color: var(--text); font: inherit; font-size: var(--text-sm); padding: 11px 0;
  }

  .onb-pills { display: flex; flex-wrap: wrap; gap: 8px; }
  .onb-pill {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    padding: 7px 14px; border-radius: var(--radius-pill);
    border: 1.5px solid var(--line); background: var(--surface); color: var(--text);
    font: inherit; font-size: var(--text-sm); font-weight: var(--fw-semibold);
    transition: all var(--dur-fast) var(--ease-out);
  }
  .onb-pill:hover { border-color: var(--accent); }
  .onb-pill.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }

  /* Buttons */
  .onb-btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 7px; cursor: pointer;
    border: 1px solid var(--accent); border-radius: var(--radius-sm); padding: 9px 16px;
    font: inherit; font-size: var(--text-sm); font-weight: var(--fw-semibold);
    transition: background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
  }
  .onb-btn.lg { padding: 11px 20px; font-size: var(--text-base); }
  .onb-btn.primary { background: var(--accent); color: var(--text-on-accent); box-shadow: var(--shadow-accent); }
  .onb-btn.primary:hover { background: var(--accent-hover); }
  .onb-btn.primary:disabled { opacity: .5; cursor: default; box-shadow: none; }
  .onb-btn.ghost { background: none; border-color: transparent; color: var(--text-muted); box-shadow: none; }
  .onb-btn.ghost:hover { color: var(--accent); }
  .onb-welcomeactions { display: flex; gap: 10px; margin-top: 4px; }

  /* Ready */
  .onb-readyhead { display: flex; align-items: center; gap: 14px; }
  .onb-readytick {
    display: inline-flex; align-items: center; justify-content: center;
    width: 52px; height: 52px; flex: none; border-radius: var(--radius-pill);
    background: var(--accent); color: var(--text-on-accent);
  }
  .onb-summary {
    display: flex; flex-direction: column; gap: 12px;
    background: var(--surface-sunk); border: 1px solid var(--line);
    border-radius: var(--radius-md); padding: 16px;
  }
  .onb-sumrow { display: flex; align-items: center; gap: 10px; }
  .onb-sumicon { color: var(--accent); display: inline-flex; }
  .onb-sumkey { width: 64px; flex: none; font-size: var(--text-sm); color: var(--text-muted); }
  .onb-sumval { flex: 1; font-size: var(--text-sm); color: var(--text); }
  .onb-sumval.cap { text-transform: capitalize; }
  .onb-tip { display: flex; align-items: center; gap: 8px; font-size: var(--text-xs); color: var(--text-muted); }
  .onb-tip b { color: var(--text); }

  /* Footer nav */
  .onb-nav { display: flex; align-items: center; gap: 10px; padding: 18px 44px; border-top: 1px solid var(--line); }
  .onb-navright { margin-left: auto; display: flex; align-items: center; gap: 12px; }

  @media (max-width: 760px) {
    .onb-hero { display: none; }
  }
</style>
