<script>
  // Cowork-style task editor: a modal over the (read-only) TaskPage, covering both
  // create (`task = null`) and edit. Mirrors Settings' modal chrome (.modal-backdrop
  // + .modal + .modal-x, Esc-to-close) rather than inventing new modal mechanics.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'
  import FolderPicker from '../FolderPicker.svelte'
  import AccessSwitch from '../AccessSwitch.svelte'
  import ScheduleField from './ScheduleField.svelte'

  let { task = null, onSaved, onClose } = $props()

  // Local editable copy — snapshotted once at mount (this modal is remounted fresh
  // each time it opens, so there's no need to react to `task` changing underneath).
  // svelte-ignore state_referenced_locally
  const initial = task
  let name = $state(initial?.name || '')
  let description = $state(initial?.description || '')
  let prompt = $state(initial?.prompt || '')
  let model = $state(initial?.model ?? null)
  // $state.snapshot, not structuredClone: `task` arrives as a $state proxy from
  // TaskPage, and structuredClone throws DataCloneError on proxies.
  let schedule = $state(initial ? $state.snapshot(initial.schedule) : { kind: 'manual', at: null, cron: null })
  let workdir = $state(initial?.workdir || '')
  let access = $state(initial?.workdir_access || 'read')

  let pickerOpen = $state(false)   // inline FolderPicker section, only relevant while no folder is set
  let models = $state([])          // llm_configs entries for the model dropdown
  let roots = $state({})           // fs roots for the FolderPicker (cwd/home/workspace)
  let saving = $state(false)
  let error = $state('')

  onMount(async () => {
    try { models = (await api.llmConfigs()).configs || [] } catch { models = [] }
    try { roots = (await api.settings()).fs || {} } catch { roots = {} }
  })

  // AccessSwitch cycles Read → Read+write → Off → Read; "Off" is this control's
  // built-in way of saying "no access", which for a task's single folder slot means
  // detaching it entirely — so it also clears `workdir`, dropping back to the
  // "Add working folder" button instead of leaving an access-less folder set.
  function onAccessChange(next) {
    if (next === null) workdir = ''
    else access = next
  }

  async function save() {
    const p = prompt.trim()
    if (!p || saving) return
    saving = true
    error = ''
    try {
      if (!task) {
        const created = await api.createTask({
          name: name.trim(),
          description: description.trim(),
          prompt: p,
          model: model ?? '',
          schedule,
          workdir: workdir || null,
          workdir_access: workdir ? access : null,
        })
        onSaved(created)
      } else {
        // workdir: '' is the PATCH route's explicit detach sentinel (null means
        // "leave unchanged") — only send it when a folder was set before and the
        // user has since removed it; otherwise null correctly means "no change".
        const removedFolder = !workdir && !!task.workdir
        const patch = {
          prompt: p,
          description: description.trim(),
          model: model ?? '',
          schedule,
          workdir: workdir || (removedFolder ? '' : null),
          workdir_access: workdir ? access : null,
        }
        const n = name.trim()
        if (n) patch.name = n   // blank name on edit leaves the existing name alone (auto-naming is create-only)
        const updated = await api.updateTask(task.id, patch)
        onSaved(updated)
      }
    } catch (e) { error = e.message || 'save failed' }
    finally { saving = false }
  }

  function onKey(e) { if (e.key === 'Escape') onClose() }
</script>

<svelte:window onkeydown={onKey} />
<div class="modal-backdrop" onclick={onClose}></div>
<div class="modal taskedit">
  <button class="modal-x" aria-label="Close" onclick={onClose}>×</button>
  <h2>{task ? 'Edit task' : 'New task'}</h2>
  {#if error}<div class="taskerror"><Icon name="x" size={13} /> {error}</div>{/if}

  <label class="tefield">Name
    <input type="text" bind:value={name} placeholder="Generated from the prompt" />
  </label>
  <label class="tefield">Description
    <input type="text" bind:value={description} placeholder="Generated from the prompt" />
  </label>
  <label class="tefield">Instructions
    <textarea rows="6" bind:value={prompt}
      placeholder="What should the agent do on every run? Be specific — it runs unattended."></textarea>
  </label>

  <div class="terow">
    {#if workdir}
      <span class="tefolder" title={workdir}><Icon name="folder" size={14} /> {workdir}</span>
      <AccessSwitch mode={access} onchange={onAccessChange} />
    {:else}
      <button class="open" onclick={() => (pickerOpen = !pickerOpen)}>
        <Icon name="folder" size={14} /> Add working folder
      </button>
    {/if}
    <label class="tefield temodel">Model
      <select class="chpick" bind:value={model}>
        <option value={null}>Profile default</option>
        {#each models as m (m.id)}<option value={m.id}>{m.name} ({m.model})</option>{/each}
      </select>
    </label>
  </div>

  {#if pickerOpen && !workdir}
    <div class="tepicker">
      <FolderPicker {roots} start={roots.cwd || roots.home || ''} bind:selected={workdir} />
    </div>
  {/if}

  <label class="tefield">Repeats
    <ScheduleField bind:schedule />
  </label>

  <div class="tefoot">
    <button class="open" onclick={onClose}>Cancel</button>
    <button class="open primary" disabled={!prompt.trim() || saving} onclick={save}>
      {saving ? 'Saving…' : 'Save'}
    </button>
  </div>
</div>

<style>
  .modal.taskedit { width: min(620px, 92vw); max-height: 88vh; overflow-y: auto; }
  .modal.taskedit > h2 { padding-right: 34px; }

  /* Field shape mirrors TaskPage's old .tpfield (itself modeled on Settings'
     .llmfield): stacked label + control, 12px semibold muted label, bordered
     control on the base surface, accent border + focus ring on focus. */
  .tefield { display: flex; flex-direction: column; gap: 5px; font-size: 12px; font-weight: 600; color: var(--muted); }
  .tefield input, .tefield textarea, .tefield select {
    font: inherit; font-size: 13px; font-weight: var(--fw-regular); color: var(--ink);
    min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px;
    background-color: var(--bg);
  }
  .tefield textarea { line-height: 1.5; resize: vertical; }
  .tefield select { padding-right: 30px; } /* clears the shared chevron (.chpick, app.css) */
  .tefield input:focus, .tefield textarea:focus, .tefield select:focus {
    outline: none; border-color: var(--accent); box-shadow: var(--focus-ring);
  }

  .terow { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
  .terow .temodel { flex: 1; min-width: 160px; }
  .tefolder {
    display: inline-flex; align-items: center; gap: 6px; flex: none; max-width: 220px;
    font-size: 13px; color: var(--ink); border: 1px solid var(--line); border-radius: 8px;
    padding: 7px 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    background: var(--bg); height: 33px; box-sizing: border-box;
  }
  .tepicker { border: 1px solid var(--line); border-radius: var(--radius-md); padding: 10px; background: var(--surface-sunk, var(--bg)); }

  .tefoot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
  /* The one CTA that matters gets the accent-outlined ".open.primary" treatment
     already used for Codex's single primary action / the old TaskPage save button. */
  .taskedit .open.primary { border-color: var(--accent); color: var(--accent); }
  .taskedit .open.primary:hover:not(:disabled) { background: var(--accent-soft); }

  .taskerror {
    display: flex; align-items: flex-start; gap: 7px; padding: 9px 11px;
    font-size: var(--text-sm); line-height: var(--leading-snug); color: var(--ag2-observer);
    background: color-mix(in srgb, var(--ag2-observer) 9%, transparent);
    border: 1px solid color-mix(in srgb, var(--ag2-observer) 28%, transparent);
    border-radius: var(--radius-sm); overflow-wrap: anywhere;
  }
</style>
