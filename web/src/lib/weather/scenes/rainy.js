// Rainy banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, dispose }. Steady-rain preset.

import * as THREE from 'three/webgpu'
import {
  Fn, Loop, vec2, vec3, float, uniform, time,
  positionLocal, normalView, uv, instanceIndex,
  max, mix, smoothstep, sin, fract, step, exp,
  mx_fractal_noise_float,
} from 'three/tsl'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'

const hash11 = (n) => fract(sin(n.mul(91.17)).mul(43758.5453))

export async function build(ctx) {
  const { font, temperatureText } = ctx
  const tempText = (temperatureText || '14').slice(0, 4)

  // steady-rain preset
  const SKY_TOP = 0x2f3743
  const SKY_BOTTOM = 0x5d6772
  const RAIN_FRAC = 0.78
  const RAIN_SPEED = 1.0
  const RAIN_OPACITY = 0.46
  // negative = falls toward the LEFT, matching the streaks' lean (the lean below
  // tilts them top-right → bottom-left, slope 0.16 per unit of fall)
  const WIND = -0.16
  const DROP_RATE = 1.4

  const uRainFrac = uniform(RAIN_FRAC)
  const uRainSpeed = uniform(RAIN_SPEED)
  const uRainOpacity = uniform(RAIN_OPACITY)
  const uWind = uniform(WIND)
  const uDropRate = uniform(DROP_RATE)
  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(SKY_BOTTOM)
  const camera = new THREE.PerspectiveCamera(34, 2, 0.1, 80)
  camera.position.set(0, 0.1, 8.4)

  // sky
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(48, 24), new THREE.MeshBasicNodeMaterial())
  sky.material.depthWrite = false
  sky.material.colorNode = mix(uSkyBottom, uSkyTop, smoothstep(0.0, 1.0, uv().y))
  sky.position.set(0, 0, -9)
  sky.renderOrder = -10
  scene.add(sky)

  // rain (GPU instanced)
  const RAIN_N = 1500
  const TOP = 3.4
  const SPAN = 5.4
  const streakGeo = new THREE.PlaneGeometry(0.016, 0.46)
  const rainMat = new THREE.MeshBasicNodeMaterial()
  rainMat.transparent = true
  rainMat.depthWrite = false
  const idf = float(instanceIndex)
  // rain occupies the right side only (x > 0) and extends past the frame so the
  // panel crops it — the left stays clear for the temperature
  const rx = hash11(idf.add(0.5)).mul(10.3).add(0.3)
  const rz = hash11(idf.add(1.7)).sub(0.5).mul(3.2).add(0.6)
  const speed = hash11(idf.add(2.3)).mul(3.0).add(6.0)
  const phase = hash11(idf.add(3.9)).mul(100.0)
  const fall = fract(time.mul(speed).mul(uRainSpeed).add(phase).div(SPAN)).mul(SPAN)
  const yPos = float(TOP).sub(fall)
  const xDrift = fall.mul(uWind)
  const leaned = vec3(positionLocal.x.add(positionLocal.y.mul(0.16)), positionLocal.y, positionLocal.z)
  rainMat.positionNode = leaned.add(vec3(rx.add(xDrift), yPos, rz))
  const along = uv().y
  const ends = smoothstep(0.0, 0.16, along).mul(smoothstep(1.0, 0.84, along))
  const active = step(idf, float(RAIN_N).mul(uRainFrac))
  rainMat.colorNode = vec3(0.74, 0.82, 0.92)
  rainMat.opacityNode = ends.mul(uRainOpacity).mul(active)
  const rain = new THREE.InstancedMesh(streakGeo, rainMat, RAIN_N)
  rain.frustumCulled = false
  scene.add(rain)

  // impact ripple field
  const rippleField = Fn(([q, spreadX, spreadY, offY, rate, sharp]) => {
    const acc = float(0.0).toVar()
    Loop({ start: 0, end: 7, type: 'int' }, ({ i }) => {
      const fi = float(i)
      const cyc = time.mul(rate).add(fi.mul(1.618))
      const id = cyc.floor()
      const age = cyc.fract()
      const cx = hash11(id.add(fi.mul(3.1))).sub(0.5).mul(spreadX)
      const cy = hash11(id.add(fi.mul(7.7)).add(2.3)).sub(0.5).mul(spreadY).add(offY)
      const d = q.sub(vec2(cx, cy)).length()
      const radius = age.mul(0.5)
      const band = d.sub(radius)
      const ring = exp(band.mul(band).mul(sharp.negate()))
      const onset = smoothstep(0.0, 0.05, age)
      const fade = age.oneMinus().pow(1.5)
      acc.addAssign(ring.mul(onset).mul(fade))
    })
    return acc
  })

  // wet temperature
  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(-2.2, 0.1, 1.1) // temperature reads on the LEFT
  scene.add(temperatureGroup)

  const tFace = max(normalView.z, 0.0)
  const tFres = tFace.oneMinus().pow(2.1)
  const q = positionLocal.xy
  const drops = rippleField(q, 2.8, 1.2, 0.1, uDropRate.mul(1.6), float(300.0)).mul(step(0.25, tFace))
  const rivulet = mx_fractal_noise_float(vec3(q.x.mul(9.0), q.y.mul(1.1).add(time.mul(0.55)), 0.0), 3).mul(0.5).add(0.5)
  const rivStreak = smoothstep(0.6, 0.95, rivulet).mul(tFace).mul(0.5)
  // light glyph so it reads against the dark rain sky; fresnel rim + drops sparkle
  let tColor = vec3(0.78, 0.85, 0.94)
  tColor = mix(tColor, vec3(0.95, 0.98, 1.0), tFres)
  tColor = tColor.add(drops.mul(vec3(1.0, 1.06, 1.15)).mul(1.6))
  tColor = tColor.add(rivStreak.mul(vec3(0.4, 0.48, 0.58)))
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = tColor

  // unit-size glyphs, left edge at x=0 and vertically centred; frame() scales the
  // group so the number is exactly half the panel height. Face-on, no skew.
  const geometry = new TextGeometry(tempText, {
    font, size: 1, depth: 0.14, curveSegments: 14,
    bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.018, bevelSegments: 4,
  })
  geometry.computeBoundingBox()
  const bb = geometry.boundingBox
  geometry.translate(-bb.min.x, -(bb.max.y + bb.min.y) / 2, 0)
  const glyphH = bb.max.y - bb.min.y
  const glyphW = bb.max.x - bb.min.x
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  temperatureGroup.add(text)

  function frame(t) {
    // the rain keeps falling (in-shader); gentle camera drift for a little life.
    // lookAt tracks camera.x so the drift is a pure translation.
    camera.position.x = Math.sin(t * 0.07) * 0.14
    camera.position.y = 0.1 + Math.sin(t * 0.1) * 0.04
    camera.lookAt(camera.position.x, -0.1, 0)

    // temperature: vertically centred, half the panel height, and horizontally
    // centred in the dry region between the left frame edge and where the rain
    // begins (x≈0.3). Pinned to the camera so it stays steady; camera.zoom read
    // at frame time — the engine applies its zoom knob after build() returns.
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom
    const halfHT = k * (camera.position.z - temperatureGroup.position.z)
    const scaleT = halfHT / glyphH
    const leftEdge = camera.position.x - halfHT * camera.aspect
    temperatureGroup.scale.setScalar(scaleT)
    temperatureGroup.position.x = (leftEdge + 0.3) / 2 - (glyphW * scaleT) / 2
    temperatureGroup.position.y = -0.1
  }

  return { scene, camera, frame }
}
