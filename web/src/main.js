import './design/styles.css'
import './design/palette.js'
import './app.css'
import './components/items/broadsheet.css' // shared editorial-surface shell (.bs / .bs-*)
import { mount } from 'svelte'
import App from './App.svelte'

export default mount(App, { target: document.getElementById('app') })
