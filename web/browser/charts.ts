import { mount, tick } from 'svelte'
import BasicA2UIComponent from '../src/components/items/BasicA2UIComponent.svelte'
import type { A2UIComponent } from '../src/lib/a2ui.ts'
import '../src/design/styles.css'
import '../src/app.css'

type Result = { name: string; failures: string[] }
const fixtures = document.querySelector('#fixtures')!
const results: Result[] = []
const labels = ['First', 'Second', 'Missing', 'Fourth', 'Fifth', 'Sixth', 'Seventh']
const series = [{ label: 'One', values: [2, 3, null, 0, 1, -2, 3] }, { label: 'Two', values: [1, 2, null, 2, 4, -1, 2] }]

async function settle() {
  await tick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
}

for (const theme of ['light', 'dark']) {
  for (const width of [280, 620]) {
    for (const variant of ['bar', 'stacked', 'line']) {
      for (const nesting of ['direct', 'column', 'row', 'grid']) {
        const name = `${theme}/${width}/${variant}/${nesting}`
        const target = document.createElement('section')
        target.className = 'fixture'; target.style.width = `${width}px`; target.dataset.theme = theme
        fixtures.append(target)
        const failures: string[] = []
        const components: A2UIComponent[] = [
          { id: 'root', component: 'Card', width: 'fill', child: nesting === 'direct' ? 'chart' : 'parent' },
          { id: 'parent', component: nesting === 'row' ? 'Row' : nesting === 'grid' ? 'Grid' : 'Column', columns: 2, width: 'fill', children: ['chart'], align: 'start' },
          { id: 'chart', component: 'Chart', variant: variant === 'line' ? 'line' : 'bar', stacked: variant === 'stacked', labels: { path: '/labels' }, series: { path: '/series' }, annotations: [{ index: 2.5, label: 'Lessons start' }], xLabel: 'Week', yLabel: 'Errors per 100 words', description: name, width: 'fill' },
        ]
        mount(BasicA2UIComponent, { target, props: { component: components[0], components, data: { labels, series } } })
        await settle()
        const frame = target.querySelector<HTMLElement>('.a2ui-chart-frame')!
        const plot = target.querySelector<HTMLElement>('.plot-scroll')!
        const svg = plot.querySelector('svg')!
        if (!svg || /NaN|Infinity/.test(svg.outerHTML)) failures.push('Invalid SVG geometry')
        if (!svg.querySelector('title')?.textContent?.includes(name)) failures.push('Missing accessible title')
        if (svg.querySelector('[data-category="2"]')) failures.push('Missing category drawn as zero')
        if (!svg.querySelector('.annotation')) failures.push('Missing event marker')
        for (const box of [target, frame]) {
          if (box.scrollWidth > box.clientWidth + 1) failures.push('Chart escapes its parent')
        }
        if (frame.getBoundingClientRect().right > target.getBoundingClientRect().right + 1) failures.push('Chart frame overflows')
        if (plot.scrollWidth > plot.clientWidth + 1) {
          plot.scrollLeft = plot.scrollWidth
          if (plot.scrollLeft < 1) failures.push('Dense chart cannot scroll')
          plot.scrollLeft = 0
        }
        const legend = target.querySelector<HTMLButtonElement>('.legend-item')!
        legend.click(); await settle()
        if (legend.getAttribute('aria-pressed') !== 'false' || svg.querySelector('[data-series="0"]')) failures.push('Legend did not hide series')
        legend.click(); await settle()
        if (legend.getAttribute('aria-pressed') !== 'true' || !svg.querySelector('[data-series="0"]')) failures.push('Legend did not restore series')
        target.querySelector<HTMLButtonElement>('.data-toggle')!.click(); await settle()
        const rows = target.querySelectorAll('tbody tr')
        if (rows.length !== labels.length) failures.push('Incomplete accessible data table')
        if (!rows[2].textContent?.includes('no data')) failures.push('Missing values lost in table')
        if (!rows[3].textContent?.includes('0')) failures.push('Measured zero lost in table')
        if (target.scrollWidth > target.clientWidth + 1) failures.push('Data table escapes its parent')
        results.push({ name, failures })
      }
    }
  }
}

for (const [name, values] of [['single', [5]], ['zero', [0]], ['missing', [null]], ['invalid', [1, 2]]] as const) {
  const target = document.createElement('section')
  target.className = 'fixture'; target.style.width = '280px'; fixtures.append(target)
  const component: A2UIComponent = { id: 'chart', component: 'Chart', variant: 'line', labels: { path: './labels' }, series: { path: './series' }, locale: 'ru', showLegend: false }
  mount(BasicA2UIComponent, { target, props: { component, data: { nested: { labels: ['A'], series: [{ label: 'One', values }] } }, scope: '/nested' } })
  await settle()
  const failures: string[] = []
  if (name === 'invalid') {
    if (!target.querySelector('[role="status"]') || target.querySelector('svg')) failures.push('Invalid bound data did not produce a contained error')
  } else if (!target.querySelector('svg') || /NaN|Infinity/.test(target.innerHTML)) failures.push('Single point has invalid geometry')
  if (target.querySelector('.legend')) failures.push('Hidden legend rendered')
  results.push({ name, failures })
}

Object.assign(window, { layoutResults: results })
