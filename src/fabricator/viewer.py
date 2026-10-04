"""An interactive 3D view in the browser: one HTML file, the model embedded inside it.

The model is a GLB (one named mesh per part) stored as base64 in the page. three.js is
loaded from a CDN as ES modules, so the page needs an internet connection the first time.
"""

from __future__ import annotations

import base64
import html
import json
import webbrowser
from pathlib import Path
from typing import cast

import numpy as np
import trimesh
import trimesh.visual

from .project import Project, ProjectError

THREE_VERSION = "0.160.0"
CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"


def _rgba(color: str) -> list[int]:
    c = color.lstrip("#")
    if len(c) != 6:
        c = "BBBBBB"
    return [int(c[i : i + 2], 16) for i in (0, 2, 4)] + [255]


def load_model_json(project: Project) -> dict:
    f = project.build_dir / "model.json"
    if not f.exists():
        raise ProjectError("This project hasn't been built yet. Run 'fabricator build' first.")
    return json.loads(f.read_text(encoding="utf-8"))


def build_scene(project: Project, include_reference: bool = True) -> tuple[trimesh.Scene, list[dict]]:
    """A glTF-standard scene (Y up, metres) with one node per part, named by part id.

    Returns the scene and [{id, name, color, printed}] for the parts it holds.
    """
    data = load_model_json(project)
    # Design space is Z up in millimetres; glTF is Y up in metres.
    to_gltf = trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0])
    to_gltf = trimesh.transformations.scale_matrix(0.001) @ to_gltf
    scene = trimesh.Scene()
    held = []
    for p in data["parts"]:
        if not p["printed"] and not include_reference:
            continue
        stl = project.build_dir / "parts" / f"{p['id']}.stl"
        if not stl.exists():
            continue
        mesh = cast("trimesh.Trimesh", trimesh.load(str(stl), force="mesh"))
        mesh.apply_transform(to_gltf)
        color = _rgba(p.get("color", "#BBBBBB"))
        material = trimesh.visual.material.PBRMaterial(
            baseColorFactor=color, metallicFactor=0.0, roughnessFactor=0.6, name=p["id"]
        )
        mesh.visual = trimesh.visual.TextureVisuals(material=material)
        scene.add_geometry(mesh, node_name=p["id"], geom_name=p["name"])
        held.append({"id": p["id"], "name": p["name"], "color": p.get("color", "#BBBBBB"), "printed": p["printed"]})
    if not held:
        raise ProjectError("There is nothing to show yet. Build the design first.")
    return scene, held


def glb_bytes(project: Project, include_reference: bool = True) -> tuple[bytes, list[dict]]:
    scene, held = build_scene(project, include_reference)
    return cast(bytes, scene.export(file_type="glb")), held


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - 3D view</title>
<style>
  :root { --bg:#f4f5f7; --panel:#ffffff; --ink:#1d2330; --muted:#5b6475; --line:#d9dce3; --accent:#2f6fdb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171d; --panel:#1d222b; --ink:#e8eaf0; --muted:#9aa3b5; --line:#2f3644; --accent:#6aa0ff; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.4 system-ui, Segoe UI, sans-serif; background:var(--bg); color:var(--ink);
         display:flex; height:100vh; }
  #side { width:300px; max-width:42vw; background:var(--panel); border-right:1px solid var(--line);
          padding:16px; overflow:auto; }
  #stage { flex:1; position:relative; min-width:0; }
  canvas { display:block; width:100%; height:100%; }
  h1 { font-size:18px; margin:0 0 12px; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:18px 0 6px; }
  .part { display:flex; align-items:center; gap:8px; padding:4px 0; }
  .part label { flex:1; display:flex; align-items:center; gap:8px; cursor:pointer; min-width:0; }
  .swatch { width:14px; height:14px; border-radius:3px; border:1px solid var(--line); flex:none; }
  .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .tag { color:var(--muted); font-size:12px; }
  button { font:inherit; font-size:13px; padding:3px 9px; border:1px solid var(--line); border-radius:6px;
           background:transparent; color:var(--ink); cursor:pointer; }
  button:hover { border-color:var(--accent); color:var(--accent); }
  .row { display:flex; flex-direction:column; gap:4px; margin:8px 0; }
  input[type=range] { width:100%; accent-color:var(--accent); }
  .help { color:var(--muted); font-size:13px; margin-top:16px; }
  #msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
         padding:24px; text-align:center; color:var(--muted); }
  @media (max-width: 640px) { body { flex-direction:column-reverse; } #side { width:100%; max-width:none;
          height:42vh; border-right:0; border-top:1px solid var(--line); } }
</style>
<script type="importmap">
{ "imports": { "three": "__CDN__/build/three.module.js", "three/addons/": "__CDN__/examples/jsm/" } }
</script>
</head><body>
<div id="side">
  <h1>__TITLE__</h1>
  <h2>Parts</h2>
  <div id="parts"></div>
  <h2>Look</h2>
  <div class="row"><label>Pull apart <span class="tag" id="explodeVal">0%</span>
    <input id="explode" type="range" min="0" max="100" value="0"></label></div>
  <div class="row"><label><input id="seethrough" type="checkbox"> See-through</label></div>
  <div class="row"><label>Cut through <span class="tag" id="cutVal">off</span>
    <input id="cut" type="range" min="0" max="1000" value="1000"></label></div>
  <div class="row"><button id="reset">Reset view</button> <button id="all">Show all parts</button></div>
  <p class="help">Drag to turn it, scroll to zoom, right-drag to move it.</p>
</div>
<div id="stage"><div id="msg">Loading the 3D view...</div></div>
<script id="parts-data" type="application/json">__PARTS__</script>
<script id="model-data" type="text/plain">__GLB__</script>
<script type="module">
const parts = JSON.parse(document.getElementById("parts-data").textContent);
const stage = document.getElementById("stage");
const msg = document.getElementById("msg");
let THREE, OrbitControls, GLTFLoader;
try {
  THREE = await import("three");
  ({ OrbitControls } = await import("three/addons/controls/OrbitControls.js"));
  ({ GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js"));
} catch (e) {
  msg.innerHTML = "The 3D view needs an internet connection to load its drawing library. " +
                  "Connect, then reload this page.";
  throw e;
}
const b64 = document.getElementById("model-data").textContent.trim();
const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.localClippingEnabled = true;
stage.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const bgColor = () => new THREE.Color(getComputedStyle(document.body).backgroundColor);
scene.background = bgColor();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { setTimeout(() => { scene.background = bgColor(); }, 50); });
scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(2, 4, 3); scene.add(sun);
const camera = new THREE.PerspectiveCamera(40, 1, 0.001, 100);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
const plane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 1e6);

const nodes = {};   // id -> {obj, home, dir, material, visible}
let center = new THREE.Vector3(), radius = 1, minY = 0, maxY = 1;

new GLTFLoader().parse(bin.buffer, "", gltf => {
  scene.add(gltf.scene);
  const all = new THREE.Box3().setFromObject(gltf.scene);
  all.getCenter(center); radius = all.getSize(new THREE.Vector3()).length() / 2 || 1;
  minY = all.min.y; maxY = all.max.y;
  for (const p of parts) {
    const obj = gltf.scene.getObjectByName(p.id);
    if (!obj) continue;
    const material = new THREE.MeshStandardMaterial({
      color: p.color, roughness: 0.6, metalness: 0, side: THREE.DoubleSide, flatShading: true,
      clippingPlanes: [plane], transparent: false, opacity: 1 });
    obj.traverse(o => { if (o.isMesh) o.material = material; });
    const box = new THREE.Box3().setFromObject(obj);
    const dir = box.getCenter(new THREE.Vector3()).sub(center);
    nodes[p.id] = { obj, home: obj.position.clone(), dir, material };
  }
  msg.remove();
  buildList(); resetView(); onResize(); animate();
}, err => { msg.textContent = "The 3D model couldn't be read from this file."; });

function resetView() {
  camera.position.copy(center).add(new THREE.Vector3(1, 0.8, 1.2).normalize().multiplyScalar(radius * 3.6));
  camera.near = radius / 100; camera.far = radius * 40; camera.updateProjectionMatrix();
  controls.target.copy(center); controls.update();
}
function onResize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.render(scene, camera);
}
window.addEventListener("resize", onResize);
function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }

const list = document.getElementById("parts");
function buildList() {
  for (const p of parts) {
    if (!nodes[p.id]) continue;
    const row = document.createElement("div"); row.className = "part"; row.dataset.id = p.id;
    const label = document.createElement("label");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", () => { nodes[p.id].obj.visible = cb.checked; });
    const sw = document.createElement("span"); sw.className = "swatch"; sw.style.background = p.color;
    const nm = document.createElement("span"); nm.className = "name";
    nm.textContent = p.id + "  " + p.name; nm.title = p.name;
    label.append(cb, sw, nm);
    if (!p.printed) { const t = document.createElement("span"); t.className = "tag"; t.textContent = "(not printed)"; label.append(t); }
    const only = document.createElement("button"); only.textContent = "Only this";
    only.addEventListener("click", () => setVisible(id => id === p.id));
    row.append(label, only); list.append(row);
  }
}
function setVisible(test) {
  for (const row of list.children) {
    const id = row.dataset.id, on = test(id);
    nodes[id].obj.visible = on; row.querySelector("input").checked = on;
  }
}
document.getElementById("all").addEventListener("click", () => setVisible(() => true));
document.getElementById("reset").addEventListener("click", () => {
  document.getElementById("explode").value = 0; document.getElementById("cut").value = 1000;
  document.getElementById("seethrough").checked = false;
  applyExplode(); applyCut(); applySee(); setVisible(() => true); resetView();
});

function applyExplode() {
  const v = Number(document.getElementById("explode").value) / 100;
  document.getElementById("explodeVal").textContent = Math.round(v * 100) + "%";
  for (const n of Object.values(nodes)) n.obj.position.copy(n.home).addScaledVector(n.dir, v * 1.2);
}
function applySee() {
  const on = document.getElementById("seethrough").checked;
  for (const n of Object.values(nodes)) { n.material.transparent = on; n.material.opacity = on ? 0.35 : 1; n.material.depthWrite = !on; n.material.needsUpdate = true; }
}
function applyCut() {
  const t = Number(document.getElementById("cut").value) / 1000;
  const off = t >= 0.999;
  plane.constant = off ? 1e6 : minY + (maxY - minY) * t;
  document.getElementById("cutVal").textContent = off ? "off" : Math.round(t * 100) + "% of the height";
}
document.getElementById("explode").addEventListener("input", applyExplode);
document.getElementById("seethrough").addEventListener("change", applySee);
document.getElementById("cut").addEventListener("input", applyCut);
</script>
</body></html>
"""


def make(project: Project, open_browser: bool = True) -> Path:
    """Write build/viewer.html and (optionally) open it in the browser."""
    glb, held = glb_bytes(project, include_reference=True)
    title = html.escape(project.name)
    page = (
        PAGE.replace("__TITLE__", title)
        .replace("__CDN__", CDN)
        .replace("__PARTS__", json.dumps(held).replace("</", "<\\/"))
        .replace("__GLB__", base64.b64encode(glb).decode("ascii"))
    )
    out = project.build_dir / "viewer.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())
    return out
