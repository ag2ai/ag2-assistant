<script>
  // A 2-position iOS switch (Read / Read+write) + a state label — the write
  // sibling of AccessSwitch (which is the 3-position Read · Read+write · Off
  // knob). Shared by the ChatFolders / TaskFolders editors and Settings →
  // Folders. There is no "Off" stop here: removing access is a separate Delete,
  // not a switch position. Muted like AccessSwitch (soft tinted fill + hairline
  // inset border, so the knob geometry is untouched); the label carries the
  // semantic, the track just hints at it (off = Read, green; on = Read+write, warn).
  //   mode: 'read' | 'read_write'
  //   onchange(nextMode): fired on click with the opposite mode — 'read' | 'read_write'
  let { mode, disabled = false, onchange } = $props()
  const rw = $derived(mode === 'read_write')
</script>

<div class="wsctl">
  <span class="wslabel">{rw ? 'Read + write' : 'Read'}</span>
  <button class="ws" class:on={rw} role="switch" aria-checked={rw} aria-label="Allow writing" {disabled} onclick={() => onchange?.(rw ? 'read' : 'read_write')}></button>
</div>

<style>
  .wsctl { display: inline-flex; align-items: center; gap: 10px; }
  /* Match the row's folder-name text (regular weight); a folder is never "Off"
     here, so no muted state. */
  .wslabel { min-width: 74px; text-align: right; font-size: 13px; }

  .ws { position: relative; flex: none; width: 38px; height: 22px; padding: 0; border: none; border-radius: 999px; cursor: pointer;
    background: color-mix(in srgb, var(--success) 20%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--success) 32%, var(--line));
    transition: background var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out); }
  .ws::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, .3); transition: transform var(--dur-fast) var(--ease-out); }
  .ws.on { background: color-mix(in srgb, var(--warning) 20%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 32%, var(--line)); }
  .ws.on::after { transform: translateX(16px); }
  /* No opacity dim on :disabled — a parent that briefly disables every switch
     while a change is in flight would otherwise blink them all. */
  .ws:disabled { cursor: default; }
</style>
