import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { api } from "./api";
import type {
  Build,
  CheckStatus,
  Component,
  DetailSpec,
  Doc,
  Figure,
  GenJob,
  Scene,
  SpecSummary,
} from "./types";
import ViewportView from "./ViewportView";
import JobPanel from "./JobPanel";
import type { ViewPreset, Viewport3D } from "./viewport";

import { badgeClass, badgeLabel, docSummary, partBadge, worstProv } from "./provenance";
import type { PartOrigin } from "./types";

function Badge({ prov }: { prov: string }): JSX.Element {
  return <span className={badgeClass(prov)}>{badgeLabel(prov)}</span>;
}

/* What a part is (inferred / confirmed), beside the badge for where its values come from. */
function PartBadge({ part, full }: { part: PartOrigin; full?: boolean }): JSX.Element | null {
  const pb = partBadge(part);
  return pb ? <span className={pb.cls} title={pb.text}>{full ? pb.text : pb.short}</span> : null;
}

interface Variant {
  detail_id: string;
  title: string;
  crop_id: string;
  location: string;
}

/* Specs that belong to this document: same checksum (crop ids repeat across PDFs). */
const variantsFor = (specs: SpecSummary[], sha: string): Variant[] =>
  specs
    .filter((s) => s.sha256 === sha)
    .map((s) => ({ detail_id: s.detail_id, title: s.title, crop_id: s.crop_id, location: s.origin }));

export default function App(): JSX.Element {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [spec, setSpec] = useState<DetailSpec | null>(null);
  const [crop, setCrop] = useState<string | null>(null);
  const [job, setJob] = useState<Build | null>(null);
  const [scene, setScene] = useState<Scene | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [explode, setExplode] = useState(0);
  const [clipOn, setClipOn] = useState(false);
  const [clipZ, setClipZ] = useState(600);
  const [clipMax, setClipMax] = useState(600);
  const [seqOn, setSeqOn] = useState(false);
  const [seqIdx, setSeqIdx] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [ovr, setOvr] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const disptRef = useRef<HTMLInputElement>(null);
  const explodeRef = useRef<HTMLInputElement>(null);
  const clipZRef = useRef<HTMLInputElement>(null);
  const evRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  const errTimer = useRef<number | null>(null);
  const pollToken = useRef(0);
  const genToken = useRef(0); // the generation the workbench is following
  const vpApi = useRef<Viewport3D | null>(null);
  const introNext = useRef(false);
  /* which detail the 3D on screen belongs to (the self-check renders it) */
  const shownDetail = useRef<string | null>(null);
  const [gen, setGen] = useState<GenJob | null>(null);
  const [cadJob, setCadJob] = useState<GenJob | null>(null);
  const [check, setCheck] = useState<GenJob | null>(null);
  const [solo, setSolo] = useState(false);
  const [cutaway, setCutaway] = useState(true);
  const [labels, setLabels] = useState(true);

  // Native listeners: the drive sets .value + dispatches a non-bubbling
  // 'input' event, which React synthetic onChange would miss.
  useEffect(() => {
    const ex = explodeRef.current;
    const cz = clipZRef.current;
    const onEx = () => ex && setExplode(parseFloat(ex.value));
    const onCz = () => cz && setClipZ(parseFloat(cz.value));
    ex?.addEventListener("input", onEx);
    cz?.addEventListener("input", onCz);
    return () => {
      ex?.removeEventListener("input", onEx);
      cz?.removeEventListener("input", onCz);
    };
  }, []);

  useEffect(
    () => () => {
      if (timer.current) window.clearInterval(timer.current);
      if (errTimer.current) window.clearTimeout(errTimer.current);
      pollToken.current++;
    },
    [],
  );

  const showErr = (msg: string | null): void => {
    setErr(msg);
    if (errTimer.current) window.clearTimeout(errTimer.current);
    if (msg) errTimer.current = window.setTimeout(() => setErr(null), 6000);
  };

  const upload = async (): Promise<void> => {
    const f = fileRef.current?.files?.[0];
    if (!f) {
      showErr("Choose a PDF first.");
      return;
    }
    showErr(null);
    try {
      const d = await api.uploadDocument(f);
      setDoc(d);
      setSpec(null);
      setCrop(null);
      setScene(null);
      shownDetail.current = null;
      setJob(null);
      setSelected(null);
      setGen(null);
      setVariants(variantsFor((await api.listSpecs()).specs, d.sha256));
    } catch (e) {
      showErr("Upload failed: " + (e as Error).message.slice(0, 200));
    }
  };

  /* Open a drawing's detail: `detailId` when known, since one drawing can
     have a seed and AI details; else the first detail for the drawing. */
  const selectCrop = async (
    cropId: string,
    vs: Variant[] = variants,
    detailId?: string,
  ): Promise<DetailSpec | null> => {
    setCrop(cropId);
    setSelected(null);
    setOvr(false);
    const v = vs.find((x) => x.crop_id === cropId && (!detailId || x.detail_id === detailId));
    const matched: DetailSpec | null = v ? await api.getSpec(v.detail_id) : null;
    if (!matched) {
      pollToken.current++; // a build still in flight must not land
      setSpec(null);
      setScene(null); // don't keep showing the previous detail's 3D
      shownDetail.current = null;
      setJob(null);
      return null;
    }
    const m: DetailSpec = matched;
    if (shownDetail.current !== m.detail_id) {
      pollToken.current++; // a build still in flight must not land
      setScene(null); // another detail's 3D must not stay on screen
      setJob(null);
      shownDetail.current = null;
    }
    setSpec(m);
    setVisible((prev) => {
      const next = { ...prev };
      for (const c of m.components) if (!(c.id in next)) next[c.id] = true;
      return next;
    });
    return m;
  };

  /* Follow a build; true when its 3D is on screen. */
  const poll = async (jobId: string): Promise<boolean> => {
    const my = ++pollToken.current;
    for (;;) {
      const b = await api.getBuild(jobId);
      if (my !== pollToken.current) return false;
      setJob(b);
      if (b.state === "done" || b.state === "failed") break;
      await new Promise((r) => setTimeout(r, 250));
    }
    const b = await api.getBuild(jobId);
    if (my !== pollToken.current) return false;
    setJob(b);
    if (b.state === "failed") {
      const t = await api.errorText(jobId);
      showErr("Build failed: " + t.split("\n")[0]);
      return false;
    }
    const s = await api.getScene(jobId);
    if (my !== pollToken.current) return false;
    setScene(s);
    shownDetail.current = s.detail_id;
    if (introNext.current) {
      introNext.current = false;
      window.setTimeout(() => vpApi.current?.playIntro(), 60);
    }
    setOrder(s.install_order);
    setSeqIdx(s.install_order.length - 1);
    setSpec((prev) =>
      prev ? { ...prev, verification: s.verification } : prev,
    );
    const ext = Number(s.context.illustrative_extent_z_mm || 600);
    setClipMax(ext);
    setClipZ(ext);
    if (clipZRef.current) clipZRef.current.value = String(ext);
    return true;
  };

  const build = async (): Promise<void> => {
    if (spec) await buildDetail(spec.detail_id);
  };

  /* Build a detail's 3D; true when it is on screen. Until then the 3D shown
     (if any) is stale, so the self-check refuses to run on it. */
  const buildDetail = async (detailId: string): Promise<boolean> => {
    if (!doc) return false;
    showErr(null);
    shownDetail.current = null;
    try {
      const b = await api.createBuild(
        detailId,
        doc.id,
        disptRef.current?.checked || false,
      );
      setJob(b);
      return await poll(b.id);
    } catch (e) {
      showErr("Build request failed: " + (e as Error).message.slice(0, 200));
      return false;
    }
  };

  /* Follow a generation to its end, including the runs that continue it
     when one spends its budget with work left. null if the user moved on. */
  const followGeneration = async (start: GenJob): Promise<GenJob | null> => {
    const my = ++genToken.current;
    let g = start;
    setGen(g);
    for (;;) {
      while (g.state === "queued" || g.state === "running") {
        await new Promise((r) => setTimeout(r, 1000));
        if (my !== genToken.current) return null;
        g = await api.getGenerate(g.id);
        setGen(g);
      }
      if (!g.continued_by) return g;
      g = await api.getGenerate(g.continued_by);
      setGen(g);
    }
  };

  /* A finished generation: list the drawing's details again, open its 3D. */
  const showGenerated = async (g: GenJob | null, cropId: string): Promise<void> => {
    if (!g || !doc) return;
    if (g.state !== "done" || !g.detail_id) {
      showErr("AI generation failed: " + (g.error || "unknown").slice(0, 200));
      return;
    }
    const vs = variantsFor((await api.listSpecs()).specs, doc.sha256);
    setVariants(vs);
    const m = await selectCrop(cropId, vs, g.detail_id);
    if (m) await buildDetail(m.detail_id);
  };

  /* AI: the agent reads the selected drawing and builds its detail. */
  const generateAI = async (): Promise<void> => {
    if (!doc || !crop) return;
    showErr(null);
    try {
      await showGenerated(await followGeneration(await api.generate(doc.id, crop)), crop);
    } catch (e) {
      showErr("AI generation failed: " + (e as Error).message.slice(0, 200));
    }
  };

  /* Select a drawing. If its AI detail has a run going (started before a
     reload, or from another page), pick it back up and follow it. */
  const openCrop = async (f: Figure): Promise<void> => {
    genToken.current++; // stop following another drawing's run
    setGen(null);
    await selectCrop(f.crop_id);
    if (!f.detail_id) return;
    try {
      const { jobs } = await api.activeJobs(f.detail_id);
      const running = jobs.find((j) => j.kind === "generate_detail" || j.kind === "revise_detail");
      if (running) await showGenerated(await followGeneration(await api.getGenerate(running.id)), f.crop_id);
    } catch (e) {
      showErr("Could not follow the running AI job: " + (e as Error).message.slice(0, 200));
    }
  };

  /* AI self-check: a persistent job on the server verifies this version,
     renders it in the headless viewer, asks the independent critic and
     repairs concrete issues in a new version. */
  const checkAI = async (detailId: string): Promise<void> => {
    if (!crop) return;
    try {
      let g = await api.critique(detailId);
      setCheck(g);
      while (g.state === "queued" || g.state === "running") {
        await new Promise((r) => setTimeout(r, 1000));
        g = await api.getGenerate(g.id);
        setCheck(g);
      }
      if (g.state !== "done") {
        showErr("Self-check failed: " + (g.error || "unknown").slice(0, 200));
        return;
      }
      if (g.version_id && doc) {
        // fresh list: a detail generated in this session isn't in `variants` yet
        const vs = variantsFor((await api.listSpecs()).specs, doc.sha256);
        setVariants(vs);
        const m = await selectCrop(crop, vs, detailId);
        if (m) await buildDetail(m.detail_id);
      }
    } catch (e) {
      showErr("Self-check failed: " + (e as Error).message.slice(0, 200));
    }
  };

  /* AI: re-model the selected part in CadQuery (Modal sandbox), then rebuild. */
  const remodel = async (partId: string): Promise<void> => {
    if (!spec || !crop) return;
    showErr(null);
    try {
      let g = await api.remodelPart(spec.detail_id, partId);
      setCadJob(g);
      while (g.state === "queued" || g.state === "running") {
        await new Promise((r) => setTimeout(r, 1000));
        g = await api.getGenerate(g.id);
        setCadJob(g);
      }
      if (g.state !== "done") {
        showErr("CAD re-model failed: " + (g.error || "unknown").slice(0, 200));
        return;
      }
      const m = await selectCrop(crop, variants, spec.detail_id);
      if (m) {
        setSelected(partId);
        await buildDetail(m.detail_id);
      }
    } catch (e) {
      showErr("CAD re-model failed: " + (e as Error).message.slice(0, 200));
    }
  };

  const togglePlay = (): void => {
    if (playing) {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      setPlaying(false);
      return;
    }
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setSeqOn(true);
      setSeqIdx(order.length - 1);
      return;
    }
    setSeqOn(true);
    setSeqIdx(-1);
    setPlaying(true);
    let i = -1;
    timer.current = window.setInterval(() => {
      i++;
      if (i >= order.length) {
        if (timer.current) window.clearInterval(timer.current);
        timer.current = null;
        setPlaying(false);
        return;
      }
      setSeqIdx(i);
    }, 900);
  };

  const applyParams = async (comp: Component): Promise<void> => {
    const inputs = [
      ...(
        evRef.current?.querySelectorAll<HTMLInputElement>("input[data-p]") ||
        []
      ),
    ].filter((el) => !el.disabled);
    try {
      let cur = spec;
      for (const el of inputs) {
        const pname = el.dataset.p!;
        const p = comp.params.find((x) => x.name === pname)!;
        if (parseFloat(el.value) !== p.value) {
          cur = await api.patchSpec(spec!.detail_id, {
            component_id: comp.id,
            param: pname,
            value: parseFloat(el.value),
          });
        }
      }
      if (cur) setSpec(cur);
      await build();
    } catch (e) {
      showErr("Correction rejected: " + (e as Error).message.slice(0, 200));
    }
  };

  const focus = (id: string): void => {
    setSelected(id);
    vpApi.current?.flyTo([id]);
    if (solo) vpApi.current?.isolate([id]);
  };

  const toggleSolo = (): void => {
    const on = !solo && !!selected;
    setSolo(on);
    vpApi.current?.isolate(on && selected ? [selected] : null);
  };

  const view = (v: ViewPreset): void => vpApi.current?.setView(v);

  const selComp: Component | null =
    spec?.components.find((c) => c.id === selected) || null;
  const verdict = job?.report?.verdict || "no build";
  const verdictCls =
    !job || job.state !== "done"
      ? ""
      : job.report && job.report.failed.length
        ? "fail"
        : "pass";

  const seqLabel =
    seqOn && seqIdx >= 0 && seqIdx < order.length
      ? `step ${seqIdx + 1}/${order.length}: ${order[seqIdx]}`
      : "";

  const hud = selComp ? (
    <div id="hud">
      <div className="t">
        {selComp.id}{" "}
        <span style={{ fontWeight: 400, color: "var(--muted)" }}>
          · {selComp.type}
        </span>
      </div>
      <div className="n">{selComp.note || ""}</div>
      {selComp.source_label ? (
        <div className="q">drawing: “{selComp.source_label}”</div>
      ) : (
        <div className="q">
          no drawing label — dimensions are assumptions unless marked
          drawing/user.
        </div>
      )}
    </div>
  ) : (
    <div id="hud">
      <div className="t">Generate the assembly</div>
      <div className="n">Upload → crop → components → build.</div>
    </div>
  );

  return (
    <>
      <header>
        <a href="#/" className="back">
          ← Library
        </a>
        <h1>Setout — add a detail from a PDF</h1>
        <span className="sub" id="docline">
          {doc
            ? `${doc.filename} · sha256 ${doc.sha256.slice(0, 12)}…`
            : "no document uploaded"}
        </span>
        <span id="verdict" className={verdictCls}>
          {verdict}
        </span>
      </header>
      <div id="app">
        <aside className="left">
          <h2>1 · Source PDF</h2>
          <div>
            <input type="file" id="pdf" ref={fileRef} accept="application/pdf" />
            <button id="upload" className="primary" onClick={() => void upload()}>
              Upload
            </button>
          </div>
          <div className="step" id="docmeta">
            {doc && docSummary(doc)}
          </div>
          <h2>2 · Detail variant + crop</h2>
          <div id="variants" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {variants.map((v) => (
              <button
                key={v.detail_id}
                data-detail={v.detail_id}
                title={v.location}
                className={spec?.detail_id === v.detail_id ? "primary" : ""}
                onClick={() => void selectCrop(v.crop_id, variants, v.detail_id)}
              >
                {v.title}
              </button>
            ))}
          </div>
          <div className="step" id="variantNote">
            {variants.length > 0 &&
              "Each variant is a separate spec (components, parameters, install order) from its own drawing crop — not a rename."}
          </div>
          <div id="crops">
            {!doc && (
              <div className="step">
                Upload a PDF to list its drawings.
              </div>
            )}
            {doc &&
              doc.figures.length === 0 &&
              "No drawings found in this PDF: no embedded figures and no pages of vector line work."}
            {doc?.figures.map((f) => (
              <div
                key={f.crop_id}
                className={"crop" + (crop === f.crop_id ? " sel" : "")}
                data-crop={f.crop_id}
                onClick={() => void openCrop(f)}
              >
                <img
                  loading="lazy"
                  src={api.figureUrl(doc.id, f.crop_id)}
                  alt={f.crop_id}
                />
                <div className="cap">
                  {f.crop_id} · p{f.page + 1} · {f.width}×{f.height}px
                  {f.kind === "rendered_page" && ` · rendered at ${f.dpi} dpi`}
                </div>
              </div>
            ))}
          </div>
          <div
            id="overlayWrap"
            style={{ display: spec ? "block" : "none", marginTop: 8 }}
          >
            {doc && crop && (
              <img id="cropimg" src={api.figureUrl(doc.id, crop)} alt="detail crop" />
            )}
            <svg id="overlay" viewBox="0 0 100 100" preserveAspectRatio="none">
              {spec?.components.flatMap((c) =>
                (c.source_regions || []).map((r, i) => (
                  <rect
                    key={`${c.id}-${i}`}
                    x={r.x0 * 100}
                    y={r.y0 * 100}
                    width={(r.x1 - r.x0) * 100}
                    height={(r.y1 - r.y0) * 100}
                    className={c.id === selected ? "sel" : ""}
                    onClick={() => focus(c.id)}
                  >
                    <title>{c.id}</title>
                  </rect>
                )),
              )}
            </svg>
          </div>
          <div
            className="step"
            id="overlayNote"
            style={{ display: spec ? "block" : "none" }}
          >
            Boxes are indicative regions from the spec — not SAM masks.
          </div>
          <h2>3 · Components</h2>
          {gen?.run_id && <JobPanel jobId={gen.id} />}
          {spec?.origin.startsWith("ai-generated") && (
            <div className="step ai-note">
              AI-generated from this drawing (the Muse agent read it, wrote the program
              through code gates, then rendered and critiqued its own 3D). Unreviewed. A <b>drawing</b> value was confirmed against the printed
              words; everything else is an <b>assumption</b>.
              <div style={{ marginTop: 6 }}>
                <button
                  id="checkAI"
                  disabled={
                    !scene ||
                    scene.detail_id !== spec.detail_id ||
                    (!!check && (check.state === "queued" || check.state === "running"))
                  }
                  onClick={() => spec && void checkAI(spec.detail_id)}
                  title="Verify, render and critique this version on the server; repairs become a new version"
                >
                  Check against drawing (AI)
                </button>
              </div>
              {check && (
                <div id="checkLog">
                  <JobPanel jobId={check.id} />
                </div>
              )}
            </div>
          )}
          <div id="comps">
            {!spec && (
              <div className="step">
                {crop ? (
                  <>
                    No detail for drawing <b>{crop}</b> yet.
                    <div style={{ marginTop: 8 }}>
                      <button
                        id="genAI"
                        className="primary"
                        disabled={!!gen && (gen.state === "queued" || gen.state === "running")}
                        onClick={() => void generateAI()}
                      >
                        Generate 3D with AI
                      </button>
                    </div>
                    {gen && !gen.run_id && (
                      <div id="genProgress" className="genlog">
                        {gen.progress.map((l, i) => (
                          <div key={i}>{l}</div>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  "Select a drawing to open its detail, or generate one with AI."
                )}
              </div>
            )}
            {spec?.components.map((c) => (
              <div
                key={c.id}
                className={"comp" + (c.id === selected ? " sel" : "")}
                onClick={() => focus(c.id)}
              >
                <input
                  type="checkbox"
                  aria-label={`toggle ${c.id}`}
                  checked={visible[c.id] !== false}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) =>
                    setVisible((prev) => ({ ...prev, [c.id]: e.target.checked }))
                  }
                />
                <span className="sw" style={{ background: c.color }} />
                <span>{c.id}</span>
                <PartBadge part={c} />
                <Badge prov={worstProv(c.params.map((p) => p.provenance))} />
              </div>
            ))}
          </div>
          <h2>4 · Build</h2>
          <button
            id="build"
            className="primary"
            disabled={!spec}
            onClick={() => void build()}
          >
            Generate assembly
          </button>
          <label
            style={{ fontSize: 12, color: "var(--muted)", marginLeft: 8 }}
          >
            <input type="checkbox" id="dispt" ref={disptRef} /> display thickness
          </label>
          <div id="progress">
            {job?.progress.map((p, i) => (
              <div
                key={i}
                className={
                  i === job.progress.length - 1 && job.state === "running"
                    ? "run"
                    : ""
                }
              >
                ▸ {p}
              </div>
            ))}
            {job && (
              <div>
                state: <b>{job.state}</b>
              </div>
            )}
          </div>
          <div id="downloads" style={{ marginTop: 8, fontSize: 12 }}>
            {job?.state === "done" &&
              job.artifacts &&
              job.artifacts.length > 0 && (
                <>
                  artifacts:{" "}
                  {job.artifacts.map((a, i) => (
                    <span key={a}>
                      {i > 0 && " · "}
                      <a href={api.artifactUrl(job.id, a)} download>
                        {a}
                      </a>
                    </span>
                  ))}
                </>
              )}
          </div>
        </aside>
        <main>
          <div id="err" style={{ display: err ? "block" : "none" }}>
            {err}
          </div>
          <div id="toolbar">
            <span id="sceneTitle">
              {scene ? `${scene.title} · ${scene.detail_id}` : "no assembly"}
            </span>
            <label>
              explode{" "}
              <input type="range" id="explode" ref={explodeRef} min="0" max="300" defaultValue="0" />
            </label>
            <label>
              <input
                type="checkbox"
                id="clipOn"
                checked={clipOn}
                onChange={(e) => setClipOn(e.target.checked)}
              />{" "}
              section z&lt;
              <input
                type="range"
                id="clipZ"
                ref={clipZRef}
                min="0"
                max={clipMax}
                defaultValue={clipMax}
                style={{ width: 80 }}
              />
            </label>
            <label>
              <input
                type="checkbox"
                id="seqOn"
                checked={seqOn}
                onChange={(e) => {
                  const on = e.target.checked;
                  setSeqOn(on);
                  if (on && seqIdx < 0) setSeqIdx(order.length - 1);
                }}
              />{" "}
              install order
            </label>
            <button id="seqPlay" disabled={!scene} onClick={togglePlay}>
              {playing ? "stop" : "play"}
            </button>
            <span id="seqLabel">{seqLabel}</span>
            <span className="views">
              <button disabled={!scene} onClick={() => vpApi.current?.flyTo(null)}>fit</button>
              <button disabled={!scene} onClick={() => view("iso")}>iso</button>
              <button disabled={!scene} onClick={() => view("front")}>section</button>
              <button disabled={!scene} onClick={() => view("top")}>top</button>
              <button disabled={!scene || !selected} className={solo ? "on" : ""} onClick={toggleSolo}>
                solo
              </button>
              <button
                disabled={!scene}
                className={cutaway ? "on" : ""}
                onClick={() => {
                  setCutaway(!cutaway);
                  vpApi.current?.setCutaway(!cutaway);
                }}
              >
                cutaway
              </button>
              <button
                disabled={!scene}
                className={labels ? "on" : ""}
                onClick={() => {
                  setLabels(!labels);
                  vpApi.current?.setLabels(!labels);
                }}
              >
                labels
              </button>
              <button disabled={!scene} onClick={() => vpApi.current?.playIntro()}>build ▶</button>
            </span>
          </div>
          <ViewportView
            scene={scene}
            visible={visible}
            explode={explode}
            clipOn={clipOn}
            clipZ={clipZ}
            seqOn={seqOn}
            seqIdx={seqIdx}
            selected={selected}
            onPick={setSelected}
            apiRef={vpApi}
            hud={hud}
          />
        </main>
        <aside className="right">
          <h2>Evidence</h2>
          <div id="evidence" ref={evRef}>
            {!selComp && (
              <div className="step">
                {spec
                  ? "Nothing selected."
                  : "Click a component in the list, the crop overlay, or the 3D view."}
              </div>
            )}
            {selComp && spec && (
              <>
                <div className="kv">
                  <span className="k">component:</span> <b>{selComp.id}</b> (
                  {selComp.type})
                </div>
                <div className="kv">
                  <span className="k">drawing label:</span>{" "}
                  {selComp.source_label ? (
                    <>“{selComp.source_label}”</>
                  ) : (
                    <i>none — not invented</i>
                  )}
                </div>
                {selComp.note && (
                  <div className="kv">
                    <span className="k">role:</span> {selComp.note}
                  </div>
                )}
                <div className="kv">
                  <span className="k">representation:</span>{" "}
                  {selComp.representation} · generator {selComp.generator.id}{" "}
                  {selComp.generator.version}
                </div>
                {selComp.class_id && (
                  <div className="kv">
                    <span className="k">class:</span> {selComp.class_id}
                  </div>
                )}
                {partBadge(selComp) && (
                  <div className="kv">
                    <span className="k">part:</span> <PartBadge part={selComp} full />
                  </div>
                )}
                {(() => {
                  const solid = scene?.solids.find((x) => x.id === selComp.id);
                  return solid && solid.tri_count ? (
                    <div className="kv">
                      <span className="k">mesh:</span> {solid.tri_count} tris
                    </div>
                  ) : null;
                })()}
                <h2 style={{ marginTop: 10 }}>Parameters</h2>
                {selComp.params.map((prm) => {
                  const lock =
                    prm.provenance === "drawing" &&
                    prm.role === "physical" &&
                    !ovr;
                  return (
                    <div key={prm.name}>
                      <div className="param">
                        <Badge prov={prm.provenance} />
                        <span style={{ minWidth: 0 }}>{prm.name}</span>
                        <input
                          type="number"
                          step="any"
                          key={`${selComp.id}-${prm.name}`}
                          defaultValue={prm.value}
                          data-p={prm.name}
                          disabled={lock}
                          title={
                            lock
                              ? "drawing value — tick override to edit"
                              : undefined
                          }
                        />
                        <span className="k">
                          {prm.unit}
                          {prm.role !== "physical" ? ` · ${prm.role}` : ""}
                        </span>
                      </div>
                      {(prm.source_quote || prm.note) && (
                        <div className="kv" style={{ marginLeft: 4 }}>
                          <span className="k">↳</span>{" "}
                          {[prm.source_quote ? `“${prm.source_quote}”` : "", prm.note || ""]
                            .filter(Boolean)
                            .join(" — ")}
                        </div>
                      )}
                    </div>
                  );
                })}
                <label style={{ fontSize: 12 }}>
                  <input
                    type="checkbox"
                    id="ovr"
                    checked={ovr}
                    onChange={(e) => setOvr(e.target.checked)}
                  />{" "}
                  override drawing values
                </label>{" "}
                <button id="applyP" onClick={() => void applyParams(selComp)}>
                  Apply &amp; rebuild
                </button>
                {spec.origin.startsWith("ai-generated") && (
                  <div style={{ marginTop: 8 }}>
                    <button
                      id="cadAI"
                      disabled={!!cadJob && (cadJob.state === "queued" || cadJob.state === "running")}
                      onClick={() => void remodel(selComp.id)}
                      title="Muse writes CadQuery for this part; it runs in a no-network Modal sandbox"
                    >
                      Model with AI (CadQuery)
                    </button>
                    {cadJob && <JobPanel jobId={cadJob.id} />}
                  </div>
                )}
                <h2 style={{ marginTop: 10 }}>Product</h2>
                <div className="kv">
                  {selComp.product?.ref ? (
                    selComp.product.ref
                  ) : (
                    <i>null — unresolved, no claim made</i>
                  )}
                  {selComp.product?.note ? ` · ${selComp.product.note}` : ""}
                </div>
                <h2 style={{ marginTop: 10 }}>Relations</h2>
                {spec.relations
                  .filter((r) => r.source === selComp.id || r.target === selComp.id)
                  .map((r, i) => (
                    <div className="kv" key={i}>
                      {r.source} <b>{r.kind.replace(/_/g, " ")}</b> {r.target}
                    </div>
                  ))}
                <h2 style={{ marginTop: 10 }}>Install</h2>
                <div className="kv">
                  after: {selComp.install_after.join(", ") || "— (first)"}
                </div>
              </>
            )}
          </div>
          <h2>Verification</h2>
          <div id="checks">
            {!scene && (
              <div className="step">Checks appear after a build.</div>
            )}
            {scene && (
              <>
                <div className="step">
                  PASS {scene.verification.filter((c) => c.status === "PASS").length} · FAIL{" "}
                  {scene.verification.filter((c) => c.status === "FAIL").length} · UNVERIFIED{" "}
                  {scene.verification.filter((c) => c.status === "UNVERIFIED").length} · N/A{" "}
                  {scene.verification.filter((c) => c.status === "N/A").length}
                </div>
                {scene.verification.map((c: (typeof scene.verification)[number]) => {
                  const cls =
                    c.status === "PASS"
                      ? "pass"
                      : c.status === "FAIL"
                        ? "fail"
                        : c.status === "UNVERIFIED"
                          ? "unv"
                          : "na";
                  const st: CheckStatus = c.status;
                  return (
                    <div className={`chk ${cls}`} key={c.id}>
                      <span className={`s s-${st}`}>{st}</span> <b>[{c.stage}] {c.id}</b> —{" "}
                      {c.description}
                      {c.expected != null && (
                        <div className="kv">
                          <span className="k">expected:</span> {String(c.expected)}
                        </div>
                      )}
                      {c.actual != null && (
                        <div className="kv">
                          <span className="k">actual:</span> {String(c.actual)}
                        </div>
                      )}
                      {c.evidence && (
                        <div className="kv">
                          <span className="k">evidence:</span> {c.evidence}
                        </div>
                      )}
                    </div>
                  );
                })}
              </>
            )}
          </div>
          <h2>Unresolved</h2>
          <div id="unres">
            {!spec && <div className="step">—</div>}
            {spec?.unresolved.map((u) => (
              <div className="unres" key={u.id}>
                <b>{u.id}</b> — {u.description}
                <br />
                <span>
                  impacts: {u.impacts.join(", ") || "—"}
                  {u.blocks_geometry ? " · BLOCKS GEOMETRY" : ""}
                </span>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </>
  );
}
