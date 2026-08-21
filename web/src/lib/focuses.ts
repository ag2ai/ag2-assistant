// The single source of truth for the "What can I help with?" focus vocabulary,
// shared by onboarding (per-profile setup page) and Settings (active profile) so
// the two lists can't drift. `id` is the lowercase slug persisted server-side
// (per-profile settings.json → injected into the agent's context); `icon` is
// display only, and the label is looked up by id so it follows the UI language.
import { m } from '../paraglide/messages.js'

// One focus the user can pick; `id` is the slug persisted server-side.
export type Focus = { id: string; icon: string }

export const FOCUS: Focus[] = [
  { id: 'research', icon: 'search' },
  { id: 'coding', icon: 'code' },
  { id: 'scheduling', icon: 'clock' },
  { id: 'writing', icon: 'file-text' },
  { id: 'data', icon: 'list' },
  { id: 'images', icon: 'image' },
]

// Display labels, resolved at call time so a locale switch re-renders into it.
// Total by construction: a slug this build has no label for reads as itself.
const LABEL: Record<string, () => string> = {
  research: m.focus_research,
  coding: m.focus_coding,
  scheduling: m.focus_scheduling,
  writing: m.focus_writing,
  data: m.focus_data,
  images: m.focus_images,
}

// Human-readable label for a saved slug (unknown slugs pass through as-is).
export const focusLabel = (id: string): string => (LABEL[id] || (() => id))()
