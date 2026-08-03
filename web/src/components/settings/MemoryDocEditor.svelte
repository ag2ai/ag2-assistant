<script lang="ts">
  // Inline load/save textarea for one memory doc — shared by the Profile Memory tab
  // (per-profile persona) and Advanced's shared-identity editor. `load`/`save` are the
  // doc's endpoints; `epoch` re-loads when it changes (persona re-points on profile
  // switch, identity passes a constant).
  import { errText } from '../../lib/errors.ts'

  type Props = {
    load: () => Promise<string>
    save: (text: string) => Promise<unknown>
    hint: string
    placeholder?: string
    minHeight?: string
    epoch?: number
  }

  let { load, save, hint, placeholder = '', minHeight = '160px', epoch = 0 }: Props = $props()

  let text = $state('')
  let loading = $state(true)
  let busy = $state(false)
  let saved = $state(false)
  let err = $state('')

  async function doLoad() {
    loading = true
    err = ''
    try { text = (await load()) || '' } catch (e) { err = errText(e) }
    loading = false
  }
  $effect(() => { epoch; doLoad() })

  async function doSave() {
    err = ''
    busy = true
    try {
      await save(text)
      saved = true
      setTimeout(() => (saved = false), 1500)
    } catch (e) {
      err = errText(e)
    }
    busy = false
  }
</script>

<p class="muted docHint">{hint}</p>
{#if err}<p class="muted docErr">{err}</p>{/if}
{#if loading}
  <p class="muted" style="font-size:13px;margin:0">Loading…</p>
{:else}
  <textarea class="docArea" style="min-height:{minHeight}" bind:value={text} spellcheck="false" {placeholder}></textarea>
  <div class="docFoot">
    {#if saved}<span class="okmsg">Saved ✓</span>{/if}
    <button class="open" onclick={doSave} disabled={busy}>Save</button>
  </div>
{/if}

<style>
  .docHint { font-size: 12px; margin: 2px 0 8px; }
  .docErr { color: var(--danger); font-size: 13px; margin: 0 0 8px; }
  .docArea {
    width: 100%; resize: vertical; box-sizing: border-box;
    border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 10px 12px;
    font: inherit; font-size: 13px; line-height: 1.5; background: var(--bg); color: var(--ink);
  }
  .docArea:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .docFoot { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 8px; }
  .okmsg { color: var(--accent); font-size: 12px; }
</style>
