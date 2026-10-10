import './design/styles.css'
import './app.css'
import { mount } from 'svelte'
import { z } from 'zod'
import BasicA2UIComponent from './components/items/BasicA2UIComponent.svelte'
import { asComponent, asComponents } from './lib/a2ui.ts'
import { animations } from './store.ts'

const Payload = z.object({
  component: z.record(z.string(), z.unknown()), data: z.record(z.string(), z.unknown()),
  width: z.number(), height: z.number(), theme: z.enum(['dark', 'light']),
})
const response = await fetch('/snapshot')
if (!response.ok) throw new Error('Preview snapshot could not be loaded')
const payload = Payload.parse(await response.json())
document.documentElement.dataset.theme = payload.theme
animations.set('off')
const target = document.getElementById('preview')!
target.style.cssText = 'width:100%;max-width:1280px;box-sizing:border-box;margin:0 auto;padding:16px 24px 48px'
mount(BasicA2UIComponent, { target, props: {
  component: asComponent(payload.component), components: asComponents(payload.component._components),
  data: payload.data, passive: true,
} })
await document.fonts.ready
await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
const boxes = [...target.querySelectorAll<HTMLElement>('[data-a2ui-id]')].slice(0, 500).map(element => {
  const rect = element.getBoundingClientRect()
  const parent = element.parentElement?.getBoundingClientRect()
  return {
    id: element.dataset.a2uiId, scope: element.dataset.a2uiScope || '',
    component: element.dataset.a2uiType,
    x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height),
    scroll_width: element.scrollWidth, client_width: element.clientWidth,
    horizontal_scroll: element.scrollWidth > element.clientWidth + 1,
    escapes_parent: !!parent && (rect.left < parent.left - 1 || rect.right > parent.right + 1),
  }
})
Object.assign(window, { __a2uiPreview: {
  viewport: { width: payload.width, height: payload.height }, theme: payload.theme,
  width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
  page_overflow: document.documentElement.scrollWidth > payload.width + 1,
  boxes,
  scroll_regions: [...target.querySelectorAll<HTMLElement>('[data-a2ui-scroll-region]')].slice(0, 500).map(element => {
    const rect = element.getBoundingClientRect()
    return {
      id: element.dataset.a2uiScrollRegion, scope: element.dataset.a2uiScope || '',
      x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height),
      scroll_width: element.scrollWidth, client_width: element.clientWidth,
      horizontal_scroll: element.scrollWidth > element.clientWidth + 1,
    }
  }),
} })
