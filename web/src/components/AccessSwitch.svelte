<script lang="ts">
  // A 3-position access toggle (Read · Read+write · Off) + a state label, shared
  // by the ChatFolders modal and Settings → Folders. Clicking advances the knob
  // to the next stop; dots mark the other two. Muted like the user message
  // bubble (soft tinted fill + hairline border); the label carries the semantic.
  import type { GrantMode, Mode } from '../schemas/index.ts'

  // `mode` accepts 'none'/null/undefined as the off position; `onchange` reports
  // the next stop, with null standing for off (the caller revokes the grant).
  type Props = {
    mode?: GrantMode | null
    disabled?: boolean
    onchange?: (next: Mode | null) => void
  }
  let { mode, disabled = false, onchange }: Props = $props()

  const modeLabel = (m: Props['mode']) => (m === 'read_write' ? 'Read + write' : m === 'read' ? 'Read' : 'Off')
  const posOf = (m: Props['mode']) => (m === 'read_write' ? 1 : m === 'read' ? 0 : 2)      // Read · Read+write · Off
  const nextMode = (m: Props['mode']): Mode | null => (m === 'read' ? 'read_write' : m === 'read_write' ? null : 'read') // cycle
  const pos = $derived(posOf(mode))
</script>

<div class="sw3ctl">
  <span class="sw3label" class:muted={pos === 2}>{modeLabel(mode)}</span>
  <button class="sw3" class:off={pos === 2} class:rw={pos === 1} data-pos={pos} aria-label={`Access: ${modeLabel(mode)} — click to change`} {disabled} onclick={() => onchange?.(nextMode(mode))}>
    <span class="sw3dot" style="left:11px"></span>
    <span class="sw3dot" style="left:33px"></span>
    <span class="sw3dot" style="left:55px"></span>
    <span class="sw3knob"></span>
  </button>
</div>

<style>
  .sw3ctl { display: inline-flex; align-items: center; gap: 10px; }

  /* Read=accent · Read+write=warn · Off=grey — soft tinted fill + hairline inset
     border (so the knob geometry is untouched). */
  .sw3 { position: relative; flex: none; width: 66px; height: 22px; padding: 0; border: none; border-radius: 999px; cursor: pointer;
    background: color-mix(in srgb, var(--accent) 20%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 32%, var(--line));
    transition: background var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out); }
  .sw3.rw { background: color-mix(in srgb, var(--warning) 20%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 32%, var(--line)); }
  .sw3.off { background: color-mix(in srgb, var(--ink) 5%, var(--surface)); box-shadow: inset 0 0 0 1px var(--line); }
  /* No opacity dim on :disabled — a parent that briefly disables every switch
     while a change is in flight would otherwise blink them all. */
  .sw3:disabled { cursor: default; }
  .sw3dot { position: absolute; top: 50%; width: 4px; height: 4px; margin: -2px 0 0 -2px; border-radius: 50%; background: color-mix(in srgb, var(--ink) 30%, transparent); z-index: 1; }
  .sw3knob { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, .3); transition: transform var(--dur-fast) var(--ease-out); z-index: 2; }
  .sw3[data-pos="1"] .sw3knob { transform: translateX(22px); }
  .sw3[data-pos="2"] .sw3knob { transform: translateX(44px); }

  /* Match the row's folder-name text (regular weight, inherited colour); only
     the Off state mutes. No bold, no per-state coloring. */
  .sw3label { min-width: 74px; text-align: right; font-size: 13px; }
  .sw3label.muted { color: var(--text-muted); }
</style>
