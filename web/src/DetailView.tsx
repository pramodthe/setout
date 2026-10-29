/* One detail, full screen: generated 3D, parts with their evidence, viewer
   controls, and the AI guide that can drive all of it. */

import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { api } from "./api";
import Guide from "./Guide";
import { badgeClass, badgeLabel, worstProv } from "./provenance";
import type { GuideAction, Scene } from "./types";
import type { ViewPreset, Viewport3D } from "./viewport";
import { labelText } from "./viewport";
import ViewportView from "./ViewportView";

export default function DetailView({ detailId, part }: { detailId: string; part: string | null }): JSX.Element {
  const [scene, setScene] = useState<Scene | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [explode, setExplode] = useState(0);
  const [clipOn, setClipOn] = useState(false);
  const [clipZ, setClipZ] = useState(300);
  const [seqOn, setSeqOn] = useState(false);
  const [seqIdx, setSeqIdx] = useState(-1);
  const [solo, setSolo] = useState(false);
  const vp = useRef<Viewport3D | null>(null);
  const [visible] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setScene(null);
    api
      .scene(detailId)
      .then((s) => {
        setScene(s);
        setClipZ(Number(s.context.illustrative_extent_z_mm || 600) / 2);
        if (part) {
          window.setTimeout(() => {
            setSelected(part);
            vp.current?.isolate([part]);
            vp.current?.flyTo([part]);
            setSolo(true);
          }, 2600);
        }
      })
      .catch((e: Error) => setErr(e.message));
  }, [detailId, part]);

  const order = scene?.install_order || [];
  const solids = scene?.solids || [];
  const sel = solids.find((s) => s.id === selected) || null;
  const extent = Number(scene?.context.illustrative_extent_z_mm || 600);
  const counts = (scene?.verification || []).reduce<Record<string, number>>(
    (a, c) => ({ ...a, [c.status]: (a[c.status] || 0) + 1 }),
    {},
  );
  const situation = (scene?.context.situation as string[] | undefined) || [];

  const focus = (id: string) => {
    setSelected(id);
    vp.current?.flyTo([id]);
    if (solo) vp.current?.isolate([id]);
  };

  /* Everything the guide can do to the viewer. */
  const run = (a: GuideAction) => {
    const v = vp.current;
    if (!v) return;
    switch (a.type) {
      case "focus":
        setSelected(a.ids[0]);
        v.flyTo(a.ids);
        break;
      case "isolate":
        setSolo(true);
        v.isolate(a.ids);
        v.flyTo(a.ids);
        break;
      case "select":
        setSelected(a.ids[0]);
        break;
      case "show_all":
        setSolo(false);
        v.isolate(null);
        setExplode(0);
        setClipOn(false);
        setSeqOn(false);
        v.setView("iso");
        break;
      case "explode":
        setExplode(a.value);
        break;
      case "section":
        if (a.value > 0) {
          setClipOn(true);
          setClipZ(Math.min(extent, a.value));
        } else setClipOn(false);
        break;
      case "view":
        if (a.view) v.setView(a.view as ViewPreset);
        break;
      case "install_step": {
        setSeqOn(true);
        setSeqIdx(a.value - 1);
        const id = order[a.value - 1];
        if (id) {
          setSelected(id);
          v.flyTo([id]);
        }
        break;
      }
    }
  };

  const hud = sel ? (
    <div id="hud">
      <div className="t">{labelText(sel.id, sel.source_label)}</div>
      <div className="n">{sel.note}</div>
    </div>
  ) : null;

  return (
    <div className="dv">
      <header className="dvhead">
        <a href="#/" className="back">
          ← Library
        </a>
        <div className="ttl">
          <div className="path">{situation.join(" › ")}</div>
          <h1>{scene?.title || detailId}</h1>
        </div>
        <span className="vsum">
          {counts.PASS || 0} pass · {counts.FAIL || 0} fail · {counts.UNVERIFIED || 0} unverified
        </span>
      </header>
      {err && <div className="liberr">Could not load this detail: {err}</div>}
      <div className="dvgrid">
        <aside className="parts">
          <h2>Parts · install order</h2>
          {order
            .map((id) => solids.find((s) => s.id === id))
            .filter((s): s is NonNullable<typeof s> => !!s && s.tri_count > 0)
            .map((s, i) => (
              <div key={s.id} className={"comp" + (s.id === selected ? " sel" : "")} onClick={() => focus(s.id)}>
                <span className="step">{i + 1}</span>
                <span className="nm">{labelText(s.id, s.source_label)}</span>
                <span className={badgeClass(worstProv(s.params.map((p) => p.provenance)))}>
                  {badgeLabel(worstProv(s.params.map((p) => p.provenance)))}
                </span>
              </div>
            ))}
          {sel && (
            <div className="ev">
              <h2>Evidence · {sel.id}</h2>
              <div className="kv">
                drawing label: {sel.source_label ? <b>“{sel.source_label}”</b> : <i>none — not invented</i>}
              </div>
              <div className="kv">type: {sel.layer}</div>
              {sel.params
                .filter((p) => p.role === "physical")
                .map((p) => (
                  <div key={p.name} className="param">
                    <span className={badgeClass(p.provenance)}>{badgeLabel(p.provenance)}</span>
                    <span>
                      {p.name} = {p.value} {p.unit}
                    </span>
                    {p.source_quote && <span className="q">“{p.source_quote}”</span>}
                  </div>
                ))}
            </div>
          )}
        </aside>
        <main>
          <div className="dvtools">
            <span className="views">
              {(["iso", "front", "top"] as ViewPreset[]).map((v) => (
                <button key={v} onClick={() => vp.current?.setView(v)}>
                  {v === "front" ? "section view" : v}
                </button>
              ))}
              <button onClick={() => vp.current?.flyTo(null)}>fit</button>
              <button
                className={solo ? "on" : ""}
                disabled={!selected}
                onClick={() => {
                  const on = !solo && !!selected;
                  setSolo(on);
                  vp.current?.isolate(on && selected ? [selected] : null);
                }}
              >
                solo
              </button>
              <button onClick={() => vp.current?.playIntro()}>build ▶</button>
            </span>
            <label>
              explode
              <input type="range" min={0} max={300} value={explode} onChange={(e) => setExplode(+e.target.value)} />
            </label>
            <label>
              <input type="checkbox" checked={clipOn} onChange={(e) => setClipOn(e.target.checked)} /> cut
              <input type="range" min={0} max={extent} value={clipZ} onChange={(e) => setClipZ(+e.target.value)} />
            </label>
            <label>
              <input
                type="checkbox"
                checked={seqOn}
                onChange={(e) => {
                  setSeqOn(e.target.checked);
                  if (e.target.checked && seqIdx < 0) setSeqIdx(order.length - 1);
                }}
              />{" "}
              step
              <input
                type="range"
                min={0}
                max={Math.max(0, order.length - 1)}
                value={Math.max(0, seqIdx)}
                disabled={!seqOn}
                onChange={(e) => setSeqIdx(+e.target.value)}
              />
              {seqOn && seqIdx >= 0 && <span className="n">{seqIdx + 1}/{order.length}</span>}
            </label>
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
            onPick={focus}
            hud={hud}
            intro
            apiRef={vp}
          />
        </main>
        <Guide detailId={detailId} run={run} />
      </div>
    </div>
  );
}
