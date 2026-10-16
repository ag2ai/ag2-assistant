import { writable, type Writable } from 'svelte/store'
import type { AttachmentPayload } from '../schemas/events.ts'
import type { ThreadKind } from '../store.ts'
import type { FileRef } from './fileRefs.ts'

export type ComposerDraft = {
  text: string
  pending: { name: string; payload: AttachmentPayload }[]
  refs: FileRef[]
  selectionStart: number
  selectionEnd: number
  selectionDirection: 'forward' | 'backward' | 'none'
  scrollTop: number
  scrollLeft: number
}

export function emptyComposerDraft(): ComposerDraft {
  return {
    text: '', pending: [], refs: [], selectionStart: 0, selectionEnd: 0,
    selectionDirection: 'none', scrollTop: 0, scrollLeft: 0,
  }
}

// Drafts belong to one browser tab and retain each profile's separate threads.
export class ComposerDrafts {
  private readonly drafts = new Map<string, Writable<ComposerDraft>>()

  forThread(profile: string, kind: ThreadKind, chat: string): Writable<ComposerDraft> {
    const key = JSON.stringify([profile, kind, chat])
    let draft = this.drafts.get(key)
    if (!draft) {
      draft = writable(emptyComposerDraft())
      this.drafts.set(key, draft)
    }
    return draft
  }
}

export const composerDrafts = new ComposerDrafts()
