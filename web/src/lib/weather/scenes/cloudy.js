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
  const uCoverage = uniform(COVERAGE)     // higher = less cloud (erosion threshold)
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
  temperatureGroup.position.set(2.88, -0.28, 1.35) // in front of the cloud volume so it reads cleanly
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
  const BOX_CENTER = new THREE.Vector3(0, 0.05, -0.4)
  const BOX_HALF = new THREE.Vector3(3.4, 1.55, 1.5)
  const bmin = vec3(BOX_CENTER.x - BOX_HALF.x, BOX_CENTER.y - BOX_HALF.y, BOX_CENTER.z - BOX_HALF.z)
  const bmax = vec3(BOX_CENTER.x + BOX_HALF.x, BOX_CENTER.y + BOX_HALF.y, BOX_CENTER.z + BOX_HALF.z)

  // density at a world point: fbm shaped into a horizontal cloud layer, wind-drifted
  const densityAt = Fn(([p]) => {
    const wind = vec3(time.mul(0.05), time.mul(0.01), time.mul(-0.02))
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
  // solid bright number with subtle facing shading for 3D form — kept constant
  // (not preset-tinted) so it reads against bright cumulus and dark storm alike
  const tFace = max(normalView.z, 0.0)
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = mix(vec3(0.78, 0.82, 0.86), vec3(1.0, 1.0, 1.0), tFace)

  const geometry = new TextGeometry(tempText, {
    font, size: 0.92, depth: 0.16, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.02, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  geometry.translate(-(box.max.x - box.min.x) / 2, -(box.max.y - box.min.y) / 2, 0)

  // dark inverted-hull outline behind the glyphs so the number pops on any cloud
  const outlineMaterial = new THREE.MeshBasicNodeMaterial()
  outlineMaterial.colorNode = vec3(0.12, 0.14, 0.16)
  outlineMaterial.side = THREE.BackSide
  const outline = new THREE.Mesh(geometry, outlineMaterial)
  outline.rotation.set(0.03, -0.16, 0)
  outline.scale.setScalar(1.09)
  outline.position.z = -0.04
  outline.renderOrder = 999
  temperatureGroup.add(outline)

  const text = new THREE.Mesh(geometry, temperatureMaterial)
  text.rotation.y = -0.16
  text.rotation.x = 0.03
  text.renderOrder = 1000
  temperatureGroup.add(text)

  function frame(t) {
    temperatureGroup.position.y = -0.3 + Math.sin(t * 0.72) * 0.03
    temperatureGroup.position.x = 2.88 + Math.sin(t * 0.4) * 0.05
    temperatureGroup.rotation.y = -0.0 + Math.sin(t * 0.18) * 0.05
    // gentle camera drift for parallax through the volume
    camera.position.x = Math.sin(t * 0.08) * 0.3
    camera.position.y = 0.24 + Math.sin(t * 0.11) * 0.06
    camera.lookAt(0, 0.0, -0.4)
  }

  return { scene, camera, frame }
}
