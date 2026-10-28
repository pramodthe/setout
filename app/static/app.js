/* Setout viewer: upload -> crop -> spec -> build -> inspect. No mocks:
   every panel renders backend state. */
'use strict';
const $ = id => document.getElementById(id);
const K = 0.01; // mm -> scene units (== app/units.py MM_PER_VIEW_UNIT)
const S = {
  doc: null, spec: null, job: null, scene: null, crop: null,
  selected: null, visible: {}, order: [], seqIdx: -1, meshes: {},
};

function err(msg) {
  const e = $('err');
  if (!msg) { e.style.display = 'none'; return; }
  e.textContent = msg; e.style.display = 'block';
  clearTimeout(e._t); e._t = setTimeout(() => e.style.display = 'none', 6000);
}

const PROV = {drawing: 'b-draw', assumption: 'b-assump', user_input: 'b-user',
  manufacturer_doc: 'b-manu'};
const PROV_LABEL = {drawing: 'drawing', assumption: 'assumption',
  user_input: 'user', manufacturer_doc: 'mfr doc'};

function worstProv(params) {
  const ps = new Set((params || []).map(p => p.provenance));
  if (ps.size === 1) return [...ps][0];
  return 'mixed';
}
function badge(prov) {
  const cls = PROV[prov] || 'b-mixed';
  const lab = PROV_LABEL[prov] || 'mixed';
  return `<span class="badge ${cls}">${lab}</span>`;
}

/* ---------------- left panel: upload / crops / comps / build ------------ */
$('upload').onclick = async () => {
  const f = $('pdf').files[0];
  if (!f) return err('Choose a PDF first.');
  err(null);
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/api/documents', {method: 'POST', body: fd});
  if (!r.ok) return err('Upload failed: ' + (await r.text()).slice(0, 200));
  S.doc = await r.json();
  $('docline').textContent = `${S.doc.filename} · sha256 ${S.doc.sha256.slice(0, 12)}…`;
  $('docmeta').textContent =
    `${S.doc.pages} page(s), ${S.doc.figures.length} embedded figure(s). ` +
    `Crops are extracted embedded figures (lossless).`;
  renderCrops();
};

async function renderCrops() {
  const el = $('crops'); el.innerHTML = '';
  if (!S.doc.figures.length) {
    el.innerHTML = '<div class="step">No embedded figures in this PDF — ' +
      'nothing to select. Vector-only pages are not rasterized in v0.</div>';
    return;
  }
  S.doc.figures.forEach(f => {
    const d = document.createElement('div');
    d.className = 'crop'; d.dataset.crop = f.crop_id;
    d.innerHTML = `<img loading="lazy" src="/api/documents/${S.doc.id}/figures/${f.crop_id}" alt="${f.crop_id}">
      <div class="cap">${f.crop_id} · p${f.page + 1} · ${f.width}×${f.height}px</div>`;
    d.onclick = () => selectCrop(f.crop_id);
    el.appendChild(d);
  });
  // Documented variants: one button per spec, matched to its source crop.
  const list = (await (await fetch('/api/specs')).json()).specs;
  S.specList = [];
  const v = $('variants'); v.innerHTML = '';
  for (const s of list) {
    const spec = await (await fetch(`/api/specs/${s.detail_id}`)).json();
    S.specList.push({detail_id: s.detail_id, title: s.title,
      crop_id: spec.source.crop_id, context: spec.context});
  }
  S.specList.forEach(s => {
    const b = document.createElement('button');
    b.textContent = s.title; b.dataset.detail = s.detail_id;
    b.title = (s.context && s.context.location) || s.detail_id;
    b.onclick = () => selectCrop(s.crop_id);
    v.appendChild(b);
  });
  $('variantNote').textContent =
    'Each variant is a separate spec (components, parameters, install ' +
    'order) from its own drawing crop — not a rename.';
}

async function selectCrop(cropId) {
  S.crop = cropId;
  S.selected = null;
  document.querySelectorAll('.crop').forEach(
    d => d.classList.toggle('sel', d.dataset.crop === cropId));
  if (!S.specList) S.specList = (await (await fetch('/api/specs')).json()).specs;
  // Match spec by its recorded source crop (seed mapping, shown honestly).
  let matched = null;
  for (const s of S.specList) {
    const spec = await (await fetch(`/api/specs/${s.detail_id}`)).json();
    if (spec.source.crop_id === cropId) { matched = spec; break; }
  }
  if (!matched) {
    $('comps').innerHTML = `<div class="step">No specification covers crop ` +
      `<b>${cropId}</b> yet. Only the D3 seed spec exists in v0.</div>`;
    $('overlayWrap').style.display = 'none'; $('overlayNote').style.display = 'none';
    $('build').disabled = true;
    return;
  }
  S.spec = matched;
  document.querySelectorAll('#variants button').forEach(
    b => b.classList.toggle('primary', b.dataset.detail === matched.detail_id));
  $('build').disabled = false;
  $('overlayWrap').style.display = 'block'; $('overlayNote').style.display = 'block';
  $('cropimg').src = `/api/documents/${S.doc.id}/figures/${cropId}`;
  renderOverlay(); renderComps(); renderUnres(); renderEvidence();
}

function renderOverlay() {
  const svg = $('overlay'); svg.innerHTML = '';
  S.spec.components.forEach(c => {
    (c.source_regions || []).forEach(r => {
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', r.x0 * 100); rect.setAttribute('y', r.y0 * 100);
      rect.setAttribute('width', (r.x1 - r.x0) * 100);
      rect.setAttribute('height', (r.y1 - r.y0) * 100);
      if (c.id === S.selected) rect.classList.add('sel');
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      t.textContent = c.id; rect.appendChild(t);
      rect.onclick = () => select(c.id);
      svg.appendChild(rect);
    });
  });
}

function renderComps() {
  const el = $('comps'); el.innerHTML = '';
  S.spec.components.forEach(c => {
    if (!(c.id in S.visible)) S.visible[c.id] = true;
    const d = document.createElement('div');
    d.className = 'comp' + (c.id === S.selected ? ' sel' : '');
    d.innerHTML = `<input type="checkbox" ${S.visible[c.id] ? 'checked' : ''} ` +
      `aria-label="toggle ${c.id}"><span class="sw" ` +
      `style="background:${c.color}"></span><span>${c.id}</span>` +
      badge(worstProv(c.params));
    d.querySelector('input').onchange = e => {
      S.visible[c.id] = e.target.checked; applyVisibility();
    };
    d.onclick = e => { if (e.target.tagName !== 'INPUT') select(c.id); };
    el.appendChild(d);
  });
}

function renderUnres() {
  const el = $('unres');
  el.innerHTML = S.spec.unresolved.map(u =>
    `<div class="unres"><b>${u.id}</b> — ${u.description}<br>` +
    `<span>impacts: ${u.impacts.join(', ') || '—'}${u.blocks_geometry ? ' · BLOCKS GEOMETRY' : ''}</span></div>`
  ).join('') || '<div class="step">none recorded</div>';
}

$('build').onclick = async () => {
  err(null);
  const r = await fetch('/api/builds', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({detail_id: S.spec.detail_id, doc_id: S.doc.id,
      use_display_thickness: $('dispt').checked})});
  if (!r.ok) return err('Build request failed: ' + (await r.text()).slice(0, 200));
  S.job = await r.json();
  poll();
};

async function poll() {
  const r = await fetch(`/api/builds/${S.job.id}`);
  const b = await r.json(); S.job = b;
  $('progress').innerHTML = b.progress.map(
    (p, i) => `<div class="${i === b.progress.length - 1 && b.state === 'running' ? 'run' : ''}">▸ ${p}</div>`
  ).join('') + `<div>state: <b>${b.state}</b></div>`;
  if (b.state === 'running' || b.state === 'queued') {
    setTimeout(poll, 250); return;
  }
  if (b.state === 'failed') {
    const e = await (await fetch(`/api/builds/${b.id}/artifacts/error.txt`)).text();
    return err('Build failed: ' + e.split('\n')[0]);
  }
  const v = $('verdict');
  v.textContent = b.report.verdict;
  v.className = b.report.failed.length ? 'fail' : 'pass';
  v.id = 'verdict';
  $('downloads').innerHTML = 'artifacts: ' + b.artifacts.map(a =>
    `<a href="/api/builds/${b.id}/artifacts/${a}" download>${a}</a>`).join(' · ');
  const s = await (await fetch(`/api/builds/${b.id}/artifacts/scene.json`)).json();
  S.scene = s; S.spec.verification = s.verification;
  S.order = s.install_order; S.seqIdx = s.install_order.length - 1;
  $('seqPlay').disabled = false;
  renderChecks(); loadMeshes();
}

function renderChecks() {
  const el = $('checks');
  const counts = {PASS: 0, FAIL: 0, UNVERIFIED: 0, 'N/A': 0};
  (S.scene.verification || []).forEach(c => counts[c.status]++);
  el.innerHTML =
    `<div class="step">PASS ${counts.PASS} · FAIL ${counts.FAIL} · ` +
    `UNVERIFIED ${counts.UNVERIFIED} · N/A ${counts['N/A']}</div>` +
    (S.scene.verification || []).map(c => {
      const cls = c.status === 'PASS' ? 'pass' : c.status === 'FAIL' ? 'fail'
        : c.status === 'UNVERIFIED' ? 'unv' : 'na';
      return `<div class="chk ${cls}"><span class="s s-${c.status}">` +
        `${c.status}</span> <b>[${c.stage}] ${c.id}</b> — ${c.description}` +
        (c.expected != null ? `<div class="kv"><span class="k">expected:</span> ${c.expected}</div>` : '') +
        (c.actual != null ? `<div class="kv"><span class="k">actual:</span> ${c.actual}</div>` : '') +
        (c.evidence ? `<div class="kv"><span class="k">evidence:</span> ${c.evidence}</div>` : '') +
        `</div>`;
    }).join('');
}

/* ---------------- selection + evidence ---------------------------------- */
function select(id) {
  S.selected = id;
  renderOverlay(); renderComps();
  Object.entries(S.meshes).forEach(([cid, mesh]) => {
    const on = cid === id;
    mesh.material.emissive = new THREE.Color(on ? 0x333333 : 0x000000);
  });
  renderEvidence();
}

function renderEvidence() {
  const el = $('evidence');
  const c = (S.spec.components || []).find(x => x.id === S.selected);
  if (!c) { el.innerHTML = '<div class="step">Nothing selected.</div>'; return; }
  const solid = (S.scene && S.scene.solids.find(x => x.id === c.id)) || null;
  const rels = (S.spec.relations || []).filter(
    r => r.source === c.id || r.target === c.id);
  el.innerHTML =
    `<div class="kv"><span class="k">component:</span> <b>${c.id}</b> (${c.type})</div>` +
    `<div class="kv"><span class="k">drawing label:</span> ` +
    (c.source_label ? `“${c.source_label}”` : '<i>none — not invented</i>') + `</div>` +
    (c.note ? `<div class="kv"><span class="k">role:</span> ${c.note}</div>` : '') +
    `<div class="kv"><span class="k">representation:</span> ${c.representation} · generator ${c.generator.id} ${c.generator.version}</div>` +
    (solid && solid.tri_count ? `<div class="kv"><span class="k">mesh:</span> ${solid.tri_count} tris</div>` : '') +
    `<h2 style="margin-top:10px">Parameters</h2>` +
    c.params.map(p => {
      const lock = p.provenance === 'drawing' && p.role === 'physical';
      return `<div class="param">${badge(p.provenance)}<span style="min-width:0">${p.name}</span>` +
        `<input type="number" step="any" value="${p.value}" data-p="${p.name}" ${lock ? 'disabled title="drawing value — tick override to edit"' : ''}>` +
        `<span class="k">${p.unit}${p.role !== 'physical' ? ' · ' + p.role : ''}</span></div>` +
        (p.source_quote || p.note ? `<div class="kv" style="margin-left:4px"><span class="k">↳</span> ${[p.source_quote ? '“' + p.source_quote + '”' : '', p.note || ''].filter(Boolean).join(' — ')}</div>` : '');
    }).join('') +
    `<label style="font-size:12px"><input type="checkbox" id="ovr"> override drawing values</label> ` +
    `<button id="applyP">Apply &amp; rebuild</button>` +
    `<h2 style="margin-top:10px">Product</h2>` +
    `<div class="kv">${c.product && c.product.ref ? c.product.ref : '<i>null — unresolved, no claim made</i>'}${c.product && c.product.note ? ' · ' + c.product.note : ''}</div>` +
    `<h2 style="margin-top:10px">Relations</h2>` +
    (rels.map(r => `<div class="kv">${r.source} <b>${r.kind.replace(/_/g, ' ')}</b> ${r.target}</div>`).join('') || '<div class="step">none</div>') +
    `<h2 style="margin-top:10px">Install</h2>` +
    `<div class="kv">after: ${c.install_after.join(', ') || '— (first)'}</div>`;
  const ovr = $('ovr');
  ovr.onchange = () => el.querySelectorAll('input[data-p]').forEach(
    i => i.disabled = !ovr.checked && c.params.find(p => p.name === i.dataset.p).provenance === 'drawing');
  $('applyP').onclick = async () => {
    const inputs = [...el.querySelectorAll('input[data-p]')].filter(i => !i.disabled);
    for (const i of inputs) {
      const p = c.params.find(p => p.name === i.dataset.p);
      if (parseFloat(i.value) !== p.value) {
        const r = await fetch(`/api/specs/${S.spec.detail_id}`, {method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({component_id: c.id, param: p.name, value: parseFloat(i.value)})});
        if (!r.ok) return err('Correction rejected: ' + (await r.text()).slice(0, 200));
        S.spec = await r.json();
      }
    }
    renderComps(); renderOverlay(); $('build').click();
  };
  const hud = $('hud');
  hud.innerHTML = `<div class="t">${c.id} <span style="font-weight:400;color:var(--muted)">· ${c.type}</span></div>` +
    `<div class="n">${c.note || ''}</div>` +
    (c.source_label ? `<div class="q">drawing: “${c.source_label}”</div>`
      : `<div class="q">no drawing label — dimensions are assumptions unless marked drawing/user.</div>`);
}

/* ---------------- 3D ------------------------------------------------------- */
const gl = $('gl');
const scene3 = new THREE.Scene();
scene3.background = new THREE.Color(0x0e1116);
scene3.add(new THREE.HemisphereLight(0xffffff, 0x27303d, 0.9));
const key = new THREE.DirectionalLight(0xffffff, 0.7);
key.position.set(6, 10, 8); scene3.add(key);
const camera = new THREE.PerspectiveCamera(42, 1, 0.05, 500);
const renderer = new THREE.WebGLRenderer({antialias: true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.localClippingEnabled = true;
gl.insertBefore(renderer.domElement, gl.firstChild);
const root = new THREE.Group(); scene3.add(root);
const clipPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 1e9);
let basePos = {};

function loadMeshes() {
  Object.values(S.meshes).forEach(m => { root.remove(m); m.geometry.dispose(); });
  S.meshes = {}; basePos = {};
  // Later-installed layers get a stronger depth bias so coplanar contact
  // faces (membrane bedded exactly on slab) don't z-fight. Render-only:
  // geometry keeps exact contact dims, verified in B-contact-*.
  const mat = (c, id) => {
    const idx = S.order.indexOf(id);
    return new THREE.MeshStandardMaterial({color: new THREE.Color(c),
      roughness: 0.85, metalness: 0.05, clippingPlanes: [clipPlane],
      polygonOffset: true, polygonOffsetFactor: -idx,
      polygonOffsetUnits: -idx});
  };
  S.scene.solids.forEach(s => {
    if (!s.tris || !s.tris.length) return; // annotations: no geometry
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(s.tris.length);
    for (let i = 0; i < s.tris.length; i++) pos[i] = s.tris[i] * K;
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.computeVertexNormals();
    const mesh = new THREE.Mesh(g, mat(s.color, s.id));
    mesh.userData = {id: s.id};
    S.meshes[s.id] = mesh; root.add(mesh);
  });
  const box = new THREE.Box3().setFromObject(root);
  const ctr = box.getCenter(new THREE.Vector3());
  root.position.sub(ctr);
  Object.entries(S.meshes).forEach(([id, m]) => basePos[id] = m.position.y);
  const size = box.getSize(new THREE.Vector3());
  radius = Math.max(size.x, size.y, size.z);
  dist = radius * 1.7;
  $('sceneTitle').textContent = `${S.scene.title} · ${S.scene.detail_id}`;
  $('clipZ').max = String(Math.round((S.scene.context.illustrative_extent_z_mm || 600)));
  $('clipZ').value = $('clipZ').max;
  applyVisibility(); applyExplode(); applyClip(); applySeq(); place();
}

function applyVisibility() {
  const seqOn = $('seqOn').checked;
  Object.entries(S.meshes).forEach(([id, m]) => {
    let v = S.visible[id] !== false;
    if (seqOn) {
      const idx = S.order.indexOf(id);
      v = v && idx >= 0 && idx <= S.seqIdx;
    }
    m.visible = v;
  });
}
function applyExplode() {
  const f = parseFloat($('explode').value) * K;
  S.order.forEach((id, i) => {
    if (S.meshes[id]) S.meshes[id].position.y = (basePos[id] || 0) + i * f / 8;
  });
}
function applyClip() {
  // Plane lives in world space; root is recentred, so convert.
  if ($('clipOn').checked) {
    clipPlane.constant = parseFloat($('clipZ').value) * K + root.position.z;
  } else clipPlane.constant = 1e9;
}
function applySeq() {
  $('seqLabel').textContent = $('seqOn').checked && S.seqIdx >= 0
    ? `step ${S.seqIdx + 1}/${S.order.length}: ${S.order[S.seqIdx]}` : '';
  applyVisibility();
}
$('explode').oninput = applyExplode;
$('clipOn').onchange = applyClip; $('clipZ').oninput = applyClip;
$('seqOn').onchange = () => { if ($('seqOn').checked && S.seqIdx < 0) S.seqIdx = S.order.length - 1; applySeq(); };
let seqTimer = null;
$('seqPlay').onclick = () => {
  if (seqTimer) { clearInterval(seqTimer); seqTimer = null; $('seqPlay').textContent = 'play'; return; }
  $('seqOn').checked = true; S.seqIdx = -1;
  $('seqPlay').textContent = 'stop';
  const step = () => {
    S.seqIdx++;
    if (S.seqIdx >= S.order.length) {
      clearInterval(seqTimer); seqTimer = null; $('seqPlay').textContent = 'play';
    }
    applySeq();
  };
  step(); seqTimer = setInterval(step, 900);
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    clearInterval(seqTimer); seqTimer = null; S.seqIdx = S.order.length - 1;
    applySeq(); $('seqPlay').textContent = 'play';
  }
};

// minimal orbit
let yaw = -0.7, pitch = 0.32, dist = 6, radius = 6, drag = false, px = 0, py = 0;
function place() {
  camera.position.set(dist * Math.cos(pitch) * Math.sin(yaw),
    dist * Math.sin(pitch), dist * Math.cos(pitch) * Math.cos(yaw));
  camera.lookAt(0, 0, 0);
}
const cv = renderer.domElement;
cv.addEventListener('pointerdown', e => { drag = true; px = e.clientX; py = e.clientY; });
addEventListener('pointerup', () => drag = false);
addEventListener('pointermove', e => {
  if (!drag) return;
  yaw -= (e.clientX - px) * 0.007;
  pitch = Math.max(-1.3, Math.min(1.3, pitch + (e.clientY - py) * 0.007));
  px = e.clientX; py = e.clientY; place();
});
cv.addEventListener('wheel', e => {
  e.preventDefault();
  dist = Math.max(radius * 0.3, Math.min(radius * 4, dist * (1 + Math.sign(e.deltaY) * 0.12)));
  place();
}, {passive: false});
function resize() {
  const w = gl.clientWidth, h = gl.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize(); place();
(function loop() { requestAnimationFrame(loop); renderer.render(scene3, camera); })();

const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
cv.addEventListener('click', e => {
  const r = cv.getBoundingClientRect();
  ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  ray.setFromCamera(ndc, camera);
  const hit = ray.intersectObjects(Object.values(S.meshes).filter(m => m.visible))[0];
  if (hit) select(hit.object.userData.id);
});
