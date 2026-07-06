// The single source of truth for the "What can I help with?" focus vocabulary,
// shared by onboarding (per-profile setup page) and Settings (active profile) so
// the two lists can't drift. `id` is the lowercase slug persisted server-side
// (per-profile settings.json → injected into the agent's context); `label`/`icon`
// are display only.
export const FOCUS = [
  { id: 'research', label: 'Research', icon: 'search' },
  { id: 'coding', label: 'Coding', icon: 'code' },
  { id: 'scheduling', label: 'Scheduling', icon: 'clock' },
  { id: 'writing', label: 'Writing', icon: 'file-text' },
  { id: 'data', label: 'Data & reports', icon: 'list' },
  { id: 'images', label: 'Images', icon: 'image' },
]

// Human-readable labels for a saved slug list (unknown slugs pass through as-is).
export const focusLabel = (id) => (FOCUS.find((f) => f.id === id) || { label: id }).label
