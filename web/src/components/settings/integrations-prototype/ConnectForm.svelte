<script>
  // PROTOTYPE — throwaway. The ONE place a token is ever typed. Picking a card from
  // the Add grid opens this; saving mints a new connection and drops you into its
  // settings. Because a platform can be connected more than once, the connection
  // gets a name here — "Telegram" tells you nothing when there are three of them.
  import { MARK_TINT } from './data.svelte.js'

  // entry: a CATALOG entry. onConnect(name, tokens) / onCancel.
  let { entry, existing = 0, onConnect, onCancel } = $props()

  let name = $state(existing ? `${entry.label} ${existing + 1}` : entry.label)
  let tokens = $state({})

  const ready = $derived(entry.fields.every((f) => (tokens[f.key] || '').trim()))
</script>

<div class="head">
  <span class="mark" style="--tint:{MARK_TINT[entry.id]}">{entry.label[0]}</span>
  <div>
    <div class="title">Connect {entry.label}</div>
    <div class="hint">{entry.setup}</div>
  </div>
</div>

<div class="form">
  <!-- Only platforms that can be connected more than once need a name. -->
  {#if entry.multiple}
    <label class="field">
      <span class="lab">Name</span>
      <input class="ctl" bind:value={name} placeholder={entry.label} />
    </label>
    <p class="hint">Yours, not the platform's — it's how this connection is listed. Rename it any time.</p>
  {/if}

  {#each entry.fields as f (f.key)}
    <label class="field">
      <span class="lab">{f.label}</span>
      <input
        type="password" class="ctl" placeholder={f.placeholder}
        bind:value={tokens[f.key]}
        onkeydown={(e) => { if (e.key === 'Enter' && ready) onConnect(name, tokens) }}
      />
    </label>
  {/each}
  <p class="hint">
    Written to the secrets store and never shown again. Swapping in a different token later
    means connecting a second {entry.label} and disconnecting this one.
  </p>

  <div class="act">
    <button class="btn primary" disabled={!ready} onclick={() => onConnect(name, tokens)}>Connect</button>
    <button class="btn ghost" onclick={onCancel}>Cancel</button>
  </div>
</div>

<style>
  .head { display: flex; align-items: center; gap: 10px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
  .title { font-size: 15px; font-weight: 600; }
  .mark {
    flex: none; display: inline-grid; place-items: center; width: 26px; height: 26px;
    border-radius: 7px; background: color-mix(in srgb, var(--tint) 22%, transparent);
    color: var(--tint); font-size: 13px; font-weight: 700;
  }
  .form { display: flex; flex-direction: column; gap: 8px; padding-top: 12px; }
  .field { display: flex; align-items: center; gap: 8px; }
  .lab { flex: none; width: 78px; font-size: 12px; color: var(--text-muted); }
  .ctl {
    flex: 1; min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px;
    background: var(--bg); color: var(--ink); font: inherit; font-size: 13px;
  }
  .ctl:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .hint { margin: 0 0 2px 86px; font-size: 12px; color: var(--text-muted); line-height: 1.45; }
  .act { display: flex; align-items: center; gap: 8px; margin: 4px 0 0 86px; }
  .btn {
    border: 1px solid var(--line); border-radius: 8px; padding: 6px 14px;
    background: var(--bg); color: var(--ink); font: inherit; font-size: 13px; cursor: pointer;
  }
  .btn.primary { border-color: var(--accent); color: var(--accent); }
  .btn:disabled { opacity: .5; cursor: default; }
  .btn.ghost { border-color: transparent; background: none; color: var(--text-muted); }
</style>
