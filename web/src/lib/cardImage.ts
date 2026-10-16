import { toBlob } from 'html-to-image'

function backgroundColor(node: HTMLElement): string {
  for (let parent: HTMLElement | null = node; parent; parent = parent.parentElement) {
    const color = getComputedStyle(parent).backgroundColor
    if (color !== 'transparent' && color !== 'rgba(0, 0, 0, 0)') return color
  }
  return getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
}

export async function cardImage(node: HTMLElement): Promise<Blob> {
  await document.fonts.ready
  const blob = await toBlob(node, {
    backgroundColor: backgroundColor(node),
    includeQueryParams: true,
    filter: child => !(child instanceof Element && child.matches(
      '.card-save-trigger, .card-save-menu, .card-image-feedback, .instance-save-result, .draft-save-result, .source-controls, .source-approval',
    )),
    style: { margin: '0' },
  })
  if (!blob) throw new Error('Could not render the card as an image. Please try again.')
  return blob
}
