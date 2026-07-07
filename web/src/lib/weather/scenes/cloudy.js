// Cloudy banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame }. Cumulus preset baked in.
//
// WebGPU + TSL true volumetric clouds: marches the view ray through a 3D fbm
// density field (a box volume) and does a secondary light-march toward the sun
// at each sample, so clouds self-shadow: dark flat bases, bright silver-lined
// tops. No post-process — engine defaults to renderer.render(scene, camera).

import * as THREE from 'three/webgpu'
import {
  Fn, Loop, If, vec3, vec4, float, uniform, time,
  positionWorld, cameraPosition, normalView, uv,
  min, max, clamp, exp, mix, smoothstep, mx_fractal_noise_float,
} from 'three/tsl'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'

export async function build(ctx) {
  const { font, temperatureText } = ctx
  const tempText = (temperatureText || '22').slice(0, 4)

  // cumulus / mostly cloudy preset (baked from applyPreset('cumulus'))
  const COVERAGE = 0.4
  const ABSORPTION = 7.0
  const LIGHT = 0xffffff
  const SHADOW = 0x6f7c88
  const AMBIENT = 0xb3bec7
  const SKY_TOP = 0x6f9ec4
  const SKY_BOTTOM = 0xdfeaf0
  const TEXT_TOP = 0xffffff
  const TEXT_BASE = 0xd7e0e5
  const TEXT_SHADOW = 0x6e7880

  // ---------------------------------------------------------------- uniforms
  // higher = less cloud (erosion threshold); jittered per build so some clouds
  // come out denser, some wispier
  const uCoverage = uniform(COVERAGE + (Math.random() - 0.5) * 0.08)
  const uSunDir = uniform(new THREE.Vector3(-0.6, 0.55, 0.4).normalize())
  const uLight = uniform(new THREE.Color(LIGHT))
  const uShadow = uniform(new THREE.Color(SHADOW))
  const uAmbient = uniform(new THREE.Color(AMBIENT))
  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))
  const uAbsorption = uniform(ABSORPTION)
  const uTextTop = uniform(new THREE.Color(TEXT_TOP))
  const uTextBase = uniform(new THREE.Color(TEXT_BASE))
  const uTextShadow = uniform(new THREE.Color(TEXT_SHADOW))

  // ---------------------------------------------------------------- scene
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(SKY_BOTTOM)

  const camera = new THREE.PerspectiveCamera(34, 2, 0.1, 80)
  camera.position.set(0, 0.24, 8.6)

  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(-2.88, -0.28, 1.35) // LEFT of the cloud volume; cloud rides right
  scene.add(temperatureGroup)

  // ---------------------------------------------------------------- sky gradient
  const sky = new THREE.Mesh(
    new THREE.PlaneGeometry(48, 24),
    new THREE.MeshBasicNodeMaterial()
  )
  sky.material.depthWrite = false
  sky.material.colorNode = mix(uSkyBottom, uSkyTop, smoothstep(0.0, 1.0, uv().y))
  sky.position.set(0, 0, -8)
  sky.renderOrder = -10
  scene.add(sky)

  // ---------------------------------------------------------------- cloud volume (raymarch)
  // Big volume: taller than the view (crops top/bottom) and long enough that the
  // right lobe is decisively cropped by the frame. The cloud stays at world x=0
  // (its position is baked into the shader); frame() pans the CAMERA left instead,
  // which slides the cloud to the right edge at any panel aspect.
  const BOX_CENTER = new THREE.Vector3(0, 0.05, -0.4)
  const BOX_HALF = new THREE.Vector3(5.6, 2.6, 1.6)
  const bmin = vec3(BOX_CENTER.x - BOX_HALF.x, BOX_CENTER.y - BOX_HALF.y, BOX_CENTER.z - BOX_HALF.z)
  const bmax = vec3(BOX_CENTER.x + BOX_HALF.x, BOX_CENTER.y + BOX_HALF.y, BOX_CENTER.z + BOX_HALF.z)

  // Random per-build seed: offsets the noise domain so every mount of the panel
  // carves a different cloud. The ellipsoid envelope below still guarantees the
  // composition (fat middle, full height, cropped right lobe) — only the billow
  // placement varies.
  const seed = vec3(Math.random() * 200 - 100, Math.random() * 200 - 100, Math.random() * 200 - 100)

  // density at a world point: fbm shaped into a horizontal cloud layer, wind-drifted
  const densityAt = Fn(([p]) => {
    const wind = vec3(time.mul(0.05), time.mul(0.01), time.mul(-0.02)).add(seed)
    // broad continuous mass + rounded billowy erosion → full but still puffy
    const base = mx_fractal_noise_float(p.mul(0.42).add(wind), 4).mul(0.5).add(0.5) // 0..1 broad shape
    const detail = mx_fractal_noise_float(p.mul(1.25).add(wind.mul(1.7)), 5).abs()  // billow detail
    const shape = base.mul(1.35).sub(detail.mul(0.3))
    const yc = BOX_CENTER.y
    // ellipsoidal envelope → rounded "american football" silhouette: fat rounded
    // middle tapering to soft points at the left/right ends, rounded top & bottom
    const ex = p.x.sub(BOX_CENTER.x).div(BOX_HALF.x * 0.98)
    const ey = p.y.sub(yc).div(BOX_HALF.y * 0.92)
    const ez = p.z.sub(BOX_CENTER.z).div(BOX_HALF.z * 0.95)
    const rr = ex.mul(ex).add(ey.mul(ey)).add(ez.mul(ez)) // squared ellipsoid radius
    const envelope = smoothstep(1.0, 0.3, rr)
    const d = shape.sub(uCoverage).max(0.0).mul(3.0)
    return clamp(d.mul(envelope), 0.0, 1.0)
  })

  const renderClouds = Fn(() => {
    const ro = cameraPosition
    const rd = positionWorld.sub(cameraPosition).normalize()

    // ray / AABB slab intersection
    const ta = bmin.sub(ro).div(rd)
    const tb = bmax.sub(ro).div(rd)
    const t1 = min(ta, tb)
    const t2 = max(ta, tb)
    const tNear = max(max(t1.x, t1.y), t1.z).max(0.0)
    const tFar = min(min(t2.x, t2.y), t2.z)

    const STEPS = 42
    const span = tFar.sub(tNear).max(0.0)
    const dt = span.div(float(STEPS))
    const t = tNear.add(dt.mul(0.5)).toVar()
    const trans = float(1.0).toVar()       // transmittance
    const accum = vec3(0.0).toVar()         // accumulated (premultiplied) colour

    Loop({ start: 0, end: STEPS, type: 'int' }, () => {
      const pos = ro.add(rd.mul(t))
      const dens = densityAt(pos).toVar()
      If(dens.greaterThan(0.002), () => {
        // light march toward the sun for self-shadowing
        const ls = float(0.2)
        const lp = pos.toVar()
        const shadow = float(0.0).toVar()
        Loop({ start: 0, end: 5, type: 'int' }, () => {
          lp.addAssign(uSunDir.mul(ls))
          shadow.addAssign(densityAt(lp))
        })
        const lightEnergy = exp(shadow.mul(ls).mul(-3.4))
        // powder term darkens dense cores, brightens thin edges (silver lining)
        const powder = float(1.0).sub(exp(dens.mul(-3.2)))
        const lit = mix(uShadow, uLight, lightEnergy).mul(uAmbient.add(lightEnergy.mul(0.85)).mul(powder.mul(0.4).add(0.7)))
        const a = dens.mul(dt).mul(uAbsorption)
        accum.addAssign(trans.mul(a).mul(lit))
        trans.mulAssign(exp(a.negate()))
      })
      t.addAssign(dt)
    })

    const alpha = float(1.0).sub(trans)
    const straight = accum.div(max(alpha, 0.001)) // un-premultiply for normal blend
    return vec4(straight, alpha)
  })

  const cloudMaterial = new THREE.MeshBasicNodeMaterial()
  cloudMaterial.transparent = true
  cloudMaterial.depthWrite = false
  cloudMaterial.side = THREE.BackSide // one fragment per covered pixel (far face)
  cloudMaterial.colorNode = renderClouds()
  const cloudVolume = new THREE.Mesh(
    new THREE.BoxGeometry(BOX_HALF.x * 2, BOX_HALF.y * 2, BOX_HALF.z * 2),
    cloudMaterial
  )
  cloudVolume.position.copy(BOX_CENTER)
  scene.add(cloudVolume)

  // ---------------------------------------------------------------- temperature text
  // dark slate number with a subtle facing lift for 3D form — reads cleanly against
  // the pale sky/cloud without needing an outline
  const tFace = max(normalView.z, 0.0)
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = mix(vec3(0.14, 0.18, 0.24), vec3(0.26, 0.32, 0.4), tFace)

  // unit-size glyphs, left edge at x=0 and vertically centred; frame() scales the
  // group so the number is exactly half the panel height. No outline, no skew —
  // the dark glyph reads on its own against the pale sky.
  const geometry = new TextGeometry(tempText, {
    font, size: 1, depth: 0.14, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.018, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const bb = geometry.boundingBox
  geometry.translate(-bb.min.x, -(bb.max.y + bb.min.y) / 2, 0)
  const glyphH = bb.max.y - bb.min.y
  const glyphW = bb.max.x - bb.min.x
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  text.renderOrder = 1000
  temperatureGroup.add(text)

  function frame(t) {
    // camera.zoom is read here (not at build) because the engine applies its zoom
    // knob after build() returns.
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom

    // pan the camera so the cloud (world x=0) rides the right edge and is cropped
    // by the frame; a tiny sway is the cloud's "small movement" (plus in-shader wind)
    const halfWc = k * (camera.position.z - BOX_CENTER.z) * camera.aspect
    const cx = 2.4 - halfWc + Math.sin(t * 0.08) * 0.15
    camera.position.x = cx
    camera.position.y = 0.24 + Math.sin(t * 0.11) * 0.05
    camera.lookAt(cx, 0.0, BOX_CENTER.z)

    // temperature: vertically centred, half the panel height, and horizontally
    // centred in the free region between the left frame edge and the cloud's
    // visible left extent (the ellipsoid envelope fades before its ±halfx tips)
    const halfHT = k * (camera.position.z - temperatureGroup.position.z)
    const scaleT = halfHT / glyphH
    const cloudLeft = -BOX_HALF.x * 0.85
    const leftEdge = cx - halfHT * camera.aspect
    temperatureGroup.scale.setScalar(scaleT)
    temperatureGroup.position.x = (leftEdge + cloudLeft) / 2 - (glyphW * scaleT) / 2
    temperatureGroup.position.y = 0
  }

  return { scene, camera, frame }
}
