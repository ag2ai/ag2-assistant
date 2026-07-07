// Sunny banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, render, dispose }. Steady preset.
// The plasma surface and temperature glyphs are TSL node shaders (MaterialX
// fractal noise for the boil), particles ride PointsNodeMaterial, and a real WebGPU
// bloom post-process pass gives genuine emissive glow.

import * as THREE from 'three/webgpu'
import {
  uniform, time, positionLocal, normalView,
  vec3, float, mix, smoothstep, max,
  mx_fractal_noise_float, pass,
} from 'three/tsl'
import { bloom } from 'three/addons/tsl/display/BloomNode.js'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'

// remap MaterialX fractal noise (~[-1,1]) into [0,1]
const fbm01 = (p, octaves) => mx_fractal_noise_float(p, octaves).mul(0.5).add(0.5)

export async function build(ctx) {
  const { renderer, font, temperatureText } = ctx
  const tempText = (temperatureText || '26').slice(0, 4)

  const uIntensity = uniform(1) // 0.7..1.9, eased toward target; drives heat + glow

  // ---------------------------------------------------------------- scene
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x160806)
  scene.fog = new THREE.FogExp2(0x2c1008, 0.052)

  const camera = new THREE.PerspectiveCamera(34, 2, 0.1, 80)
  camera.position.set(0, 0.15, 8.2)

  const root = new THREE.Group()
  root.position.y = -0.08
  scene.add(root)

  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(-2.62, -0.32, 0.18) // temperature reads on the LEFT; sun rides right
  scene.add(temperatureGroup)

  // ---------------------------------------------------------------- sun surface (TSL plasma)
  const spos = positionLocal.normalize().mul(2.0)
  const cell = fbm01(spos.mul(2.2).add(vec3(time.mul(0.28), time.mul(-0.16), time.mul(0.11))), 5)
  const boil = fbm01(spos.mul(5.0).add(vec3(time.mul(-0.55), time.mul(0.38), float(0.0))), 4)
  const face = max(normalView.z, 0.0)
  const edge = face.oneMinus().pow(1.7)
  const sunHeat = smoothstep(0.26, 1.0, cell.mul(0.72).add(boil.mul(0.62)).add(edge.mul(0.38)))
  const flame = cell.mul(0.55).add(boil.mul(0.45)) // 0..1 turbulent mask, animated

  // warm golden-yellow plasma (was red→orange); keep a little amber in the shadows
  // for depth but the ball should read yellow, not fiery
  let sunColor = mix(vec3(1.0, 0.52, 0.08), vec3(1.0, 0.78, 0.16), smoothstep(0.15, 0.68, sunHeat))
  sunColor = mix(sunColor, vec3(1.0, 0.9, 0.46), smoothstep(0.72, 1.08, boil)) // bright yellow core
  sunColor = sunColor.add(edge.mul(vec3(1.0, 0.66, 0.14)).mul(uIntensity.mul(0.32).add(0.5)))
  sunColor = sunColor.mul(uIntensity.mul(0.16).add(0.82)) // overall brightness down

  const sunMaterial = new THREE.MeshBasicNodeMaterial()
  sunMaterial.colorNode = sunColor
  sunMaterial.transparent = true
  sunMaterial.depthWrite = false
  // dissolve the silhouette into irregular flame tongues so the disc doesn't read
  // as a hard circle: opaque core, noisy alpha falloff across the outer rim band
  sunMaterial.opacityNode = smoothstep(0.0, 0.5, face.add(flame.sub(0.45).mul(0.95)))
  const sun = new THREE.Mesh(new THREE.SphereGeometry(1.18, 96, 64), sunMaterial)
  root.add(sun)

  // soft round falloff from the point sprite coord, scaled by a per-layer opacity uniform
  function makePointsMaterial(opacity, colorNode) {
    const m = new THREE.PointsNodeMaterial()
    m.transparent = true
    m.depthWrite = false
    m.blending = THREE.AdditiveBlending
    m.colorNode = colorNode
    // points carry no uv in WebGPU, so additive blending + the bloom pass do the
    // softening; opacity is a per-layer uniform we animate each frame
    const o = uniform(opacity)
    m.opacityNode = o
    m.userData.opacity = o
    return m
  }

  // rising heat shimmer
  const heatGeometry = new THREE.BufferGeometry()
  const heatCount = 180
  const heatPositions = new Float32Array(heatCount * 3)
  const heatSeeds = []
  for (let i = 0; i < heatCount; i += 1) {
    const x = -4 + Math.random() * 8
    const y = -1.4 + Math.random() * 2.8
    const z = -0.9 + Math.random() * 0.35
    heatPositions[i * 3] = x
    heatPositions[i * 3 + 1] = y
    heatPositions[i * 3 + 2] = z
    heatSeeds.push({ x, y, z, speed: 0.08 + Math.random() * 0.22, phase: Math.random() * 12 })
  }
  heatGeometry.setAttribute('position', new THREE.BufferAttribute(heatPositions, 3))
  const heatMaterial = makePointsMaterial(0.12, vec3(1.0, 0.75, 0.43))
  heatMaterial.size = 0.1
  heatMaterial.sizeAttenuation = true
  const heat = new THREE.Points(heatGeometry, heatMaterial)
  scene.add(heat)

  // ---------------------------------------------------------------- temperature text (TSL plasma)
  const tpos = positionLocal.mul(vec3(2.2, 2.8, 5.0))
  const tboil = fbm01(tpos.add(vec3(time.mul(0.32), time.mul(-0.22), time.mul(0.08))), 5)
  const tgrain = fbm01(tpos.mul(2.25).add(vec3(time.mul(-0.58), time.mul(0.36), float(0.0))), 5)
  const tface = max(normalView.z, 0.0)
  const trim = tface.oneMinus().pow(1.45)
  const theat = smoothstep(0.24, 1.0, tboil.mul(0.74).add(tgrain.mul(0.52)).add(trim.mul(0.34)))
  let tColor = mix(vec3(1.0, 0.22, 0.02), vec3(1.0, 0.66, 0.07), smoothstep(0.08, 0.62, theat))
  tColor = mix(tColor, vec3(1.0, 0.94, 0.58), smoothstep(0.56, 1.0, tgrain))
  tColor = tColor.add(trim.mul(vec3(1.0, 0.46, 0.08)).mul(uIntensity.mul(0.5).add(0.75)))
  tColor = tColor.mul(uIntensity.mul(0.18).add(1.1))

  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = tColor

  // unit-size glyphs, left edge at x=0 and vertically centred; frame() scales the
  // group so the number is exactly half the panel height regardless of aspect
  const geometry = new TextGeometry(tempText, {
    font, size: 1, depth: 0.14, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.018, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const bb = geometry.boundingBox
  geometry.translate(-bb.min.x, -(bb.max.y + bb.min.y) / 2, 0)
  const glyphH = bb.max.y - bb.min.y // measured for exact half-panel scaling
  const glyphW = bb.max.x - bb.min.x // measured for centring in the free left region
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  temperatureGroup.add(text)

  // ---------------------------------------------------------------- interaction (steady preset baked)
  let intensity = 1
  let targetIntensity = 1
  let mode = 'steady'

  // ---------------------------------------------------------------- render (bloom post-process)
  const postProcessing = new THREE.RenderPipeline(renderer)
  const scenePass = pass(scene, camera)
  const scenePassColor = scenePass.getTextureNode()
  const bloomPass = bloom(scenePassColor, 0.62, 0.55, 0.32) // strength, radius, threshold
  postProcessing.outputNode = scenePassColor.add(bloomPass)

  function frame(t) {
    intensity += (targetIntensity - intensity) * 0.035
    const stormPulse = mode === 'storm' ? 0.28 * Math.sin(t * 5.8) + 0.18 * Math.sin(t * 9.6) : 0
    const live = Math.max(0.72, intensity + stormPulse)
    uIntensity.value = live

    const hp = heatGeometry.attributes.position.array
    heatSeeds.forEach((seed, i) => {
      const drift = (t * seed.speed + seed.phase) % 1
      hp[i * 3] = seed.x + Math.sin(t * 1.2 + seed.phase) * 0.16
      hp[i * 3 + 1] = seed.y + drift * 0.58
      hp[i * 3 + 2] = seed.z
    })
    heatGeometry.attributes.position.needsUpdate = true
    heatMaterial.userData.opacity.value = 0.06 + live * 0.055

    // camera first (sun + temperature are pinned to it, so they stay frame-steady
    // while the drift gives the background a little parallax life)
    camera.position.x = Math.sin(t * 0.13) * 0.12
    camera.position.y = 0.15 + Math.sin(t * 0.17) * 0.05
    camera.lookAt(camera.position.x, -0.05, 0)

    // framing, computed from the live camera so the crop holds at any panel aspect.
    // camera.zoom is read here (not at build) because the engine applies its zoom
    // knob after build() returns.
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom
    const halfH0 = k * camera.position.z // view half-height at the sun's plane (z=0)
    const halfW0 = halfH0 * camera.aspect

    // sun: a huge plasma disc pinned past the top-right corner — the frame crops it
    const SUN_SCALE = 5.2
    const sunR = 1.18 * SUN_SCALE
    root.scale.setScalar(SUN_SCALE)
    root.position.x = camera.position.x + halfW0 * 0.98
    root.position.y = halfH0 * 1.15 + Math.sin(t * 0.5) * 0.08 // slow bob = its "small movement"
    root.rotation.y = Math.sin(t * 0.24) * 0.1
    root.rotation.z = Math.sin(t * 0.18) * 0.03
    sun.rotation.y = t * 0.16

    // temperature: vertically centred, half the panel height, and horizontally
    // centred in the free region between the left frame edge and the sun's
    // visible left boundary (its disc edge at mid-height)
    const halfHT = k * (camera.position.z - temperatureGroup.position.z)
    const scaleT = halfHT / glyphH
    const dy = root.position.y + 0.05 // sun centre vs the view's vertical centre
    const sunLeft = root.position.x - Math.sqrt(Math.max(sunR * sunR - dy * dy, 0))
    const leftEdge = camera.position.x - halfHT * camera.aspect
    temperatureGroup.scale.setScalar(scaleT)
    temperatureGroup.position.x = (leftEdge + sunLeft) / 2 - (glyphW * scaleT) / 2
    temperatureGroup.position.y = -0.05 // the camera's lookAt height = vertical centre
  }

  const render = () => postProcessing.render()

  return { scene, camera, frame, render }
}
