// One allocator for thread-item ids. project.ts, controller.ts and a2ui.js all
// append to the same keyed list, so a single counter keeps their keys unique.
let seq = 0

export function nextItemId(): number {
  return ++seq
}
