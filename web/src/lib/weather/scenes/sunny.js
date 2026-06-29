// Sunny banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, render, dispose }. Steady preset.
// The plasma surface, aura and temperature glyphs are TSL node shaders (MaterialX
// fractal noise for the boil), particles ride PointsNodeMaterial, and a real WebGPU
// bloom post-process pass gives genuine emissive glow.

import * as THREE from 'three/webgpu'
import {
  uniform, time, positionLocal, positionWorld, normalView,
  vec3, float, mix, smoothstep, max, attribute,
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
  temperatureGroup.position.set(2.62, -0.32, 0.18)
  scene.add(temperatureGroup)

  // ---------------------------------------------------------------- sun surface (TSL plasma)
  const spos = positionLocal.normalize().mul(2.0)
  const cell = fbm01(spos.mul(2.2).add(vec3(time.mul(0.28), time.mul(-0.16), time.mul(0.11))), 5)
  const boil = fbm01(spos.mul(5.0).add(vec3(time.mul(-0.55), time.mul(0.38), float(0.0))), 4)
  const face = max(normalView.z, 0.0)
  const edge = face.oneMinus().pow(1.7)
  const sunHeat = smoothstep(0.26, 1.0, cell.mul(0.72).add(boil.mul(0.62)).add(edge.mul(0.38)))
  const flame = cell.mul(0.55).add(boil.mul(0.45)) // 0..1 turbulent mask, animated

  let sunColor = mix(vec3(1.0, 0.22, 0.02), vec3(1.0, 0.62, 0.09), smoothstep(0.15, 0.68, sunHeat))
  sunColor = mix(sunColor, vec3(1.0, 0.82, 0.42), smoothstep(0.72, 1.08, boil)) // dimmer, less white-hot core
  sunColor = sunColor.add(edge.mul(vec3(1.0, 0.42, 0.07)).mul(uIntensity.mul(0.32).add(0.5)))
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

  // ---------------------------------------------------------------- aura shell (backside rim glow)
  const auraWave = positionWorld.y.mul(7.0).add(time.mul(2.2)).sin().mul(0.5).add(0.5)
    .mul(positionWorld.x.mul(5.0).sub(time.mul(1.4)).sin().mul(0.5).add(0.5))
  const auraRim = normalView.z.abs().oneMinus().pow(1.45)

  const auraMaterial = new THREE.MeshBasicNodeMaterial()
  auraMaterial.transparent = true
  auraMaterial.depthWrite = false
  auraMaterial.side = THREE.BackSide
  auraMaterial.blending = THREE.AdditiveBlending
  auraMaterial.colorNode = mix(vec3(1.0, 0.28, 0.02), vec3(1.0, 0.92, 0.42), auraWave)
  auraMaterial.opacityNode = auraRim.mul(auraWave.mul(0.22).add(0.18)).mul(uIntensity)
  const aura = new THREE.Mesh(new THREE.SphereGeometry(1.64, 96, 64), auraMaterial)
  root.add(aura)

  // ---------------------------------------------------------------- particle layers
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

  function makeParticleLayer(count, radiusMin, radiusMax, colorA, colorB, size, opacity, upwardBias) {
    const geometry = new THREE.BufferGeometry()
    const positions = new Float32Array(count * 3)
    const colors = new Float32Array(count * 3)
    const seeds = []
    for (let i = 0; i < count; i += 1) {
      const dir = new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5 + upwardBias, Math.random() - 0.5).normalize()
      const radius = radiusMin + Math.random() * (radiusMax - radiusMin)
      positions[i * 3] = dir.x * radius
      positions[i * 3 + 1] = dir.y * radius
      positions[i * 3 + 2] = dir.z * radius
      const color = colorA.clone().lerp(colorB, Math.random())
      colors[i * 3] = color.r
      colors[i * 3 + 1] = color.g
      colors[i * 3 + 2] = color.b
      seeds.push({ dir, baseRadius: radius, phase: Math.random() * Math.PI * 2, speed: 0.35 + Math.random() * 1.2, curl: Math.random() * Math.PI * 2 })
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    const material = makePointsMaterial(opacity, attribute('color', 'vec3'))
    material.size = size
    material.sizeAttenuation = true
    const points = new THREE.Points(geometry, material)
    return { geometry, material, points, seeds }
  }

  const corona = makeParticleLayer(460, 1.18, 1.95, new THREE.Color(0xff4b08), new THREE.Color(0xfff0a8), 0.11, 0.6, 0.08)
  root.add(corona.points)

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

  const geometry = new TextGeometry(tempText, {
    font, size: 0.82, depth: 0.12, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.018, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  geometry.translate(-(box.max.x - box.min.x) / 2, -(box.max.y - box.min.y) / 2, 0)
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  text.rotation.y = -0.18
  text.rotation.x = 0.03
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

  function animateLayer(layer, t, turbulence, buoyancy) {
    const p = layer.geometry.attributes.position.array
    layer.seeds.forEach((seed, i) => {
      const curl = seed.curl + t * seed.speed
      const pulse = Math.sin(t * (1.7 + seed.speed) + seed.phase) * 0.13
      const radius = seed.baseRadius * (1 + pulse * intensity)
      const tx = Math.sin(curl * 1.7 + seed.phase) * turbulence
      const ty = Math.cos(curl * 1.3) * turbulence + buoyancy * Math.sin(t * seed.speed + seed.phase)
      const tz = Math.sin(curl * 1.1 - seed.phase) * turbulence * 0.55
      p[i * 3] = seed.dir.x * radius + tx
      p[i * 3 + 1] = seed.dir.y * radius + ty
      p[i * 3 + 2] = seed.dir.z * radius + tz
    })
    layer.geometry.attributes.position.needsUpdate = true
  }

  function frame(t) {
    intensity += (targetIntensity - intensity) * 0.035
    const stormPulse = mode === 'storm' ? 0.28 * Math.sin(t * 5.8) + 0.18 * Math.sin(t * 9.6) : 0
    const live = Math.max(0.72, intensity + stormPulse)
    uIntensity.value = live

    animateLayer(corona, t, 0.16 * live, 0.08 * live)
    corona.material.userData.opacity.value = 0.38 + live * 0.17

    const hp = heatGeometry.attributes.position.array
    heatSeeds.forEach((seed, i) => {
      const drift = (t * seed.speed + seed.phase) % 1
      hp[i * 3] = seed.x + Math.sin(t * 1.2 + seed.phase) * 0.16
      hp[i * 3 + 1] = seed.y + drift * 0.58
      hp[i * 3 + 2] = seed.z
    })
    heatGeometry.attributes.position.needsUpdate = true
    heatMaterial.userData.opacity.value = 0.06 + live * 0.055

    // movement: sun sway + spin, counter-spinning aura, temperature bob
    root.rotation.y = Math.sin(t * 0.24) * 0.32
    root.rotation.z = Math.sin(t * 0.18) * 0.055
    root.position.x = Math.sin(t * 0.31) * 0.12
    root.position.y = -0.08 + Math.sin(t * 0.5) * 0.06 // gentle rise/fall
    sun.rotation.y = t * 0.16
    aura.rotation.y = -t * 0.12
    temperatureGroup.position.y = -0.32 + Math.sin(t * 0.85) * 0.05
    temperatureGroup.position.x = 2.62 + Math.sin(t * 0.4) * 0.06
    temperatureGroup.rotation.y = Math.sin(t * 0.22) * 0.08

    // ambient camera drift so the whole scene has parallax life
    camera.position.x = Math.sin(t * 0.13) * 0.28
    camera.position.y = 0.15 + Math.sin(t * 0.17) * 0.12
    camera.lookAt(0, -0.05, 0)
  }

  const render = () => postProcessing.render()

  return { scene, camera, frame, render }
}
