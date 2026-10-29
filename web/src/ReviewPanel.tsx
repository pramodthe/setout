/* Review an AI-generated detail before publishing: accept or fix each value
   (printed values first), fix labels to ones printed on the drawing, see what
   the gates and self-checks changed, then publish as reviewed. */

import { useEffect, useState } from "react";
import type { JSX } from "react";
import { api } from "./api";
import { badgeClass, badgeLabel } from "./provenance";
import type { ReviewData, ReviewParam } from "./types";

export default function ReviewPanel(p: {
  detailId: string;
  onShow: (partIds: string[]) => void; // isolate parts a value drives
  onSaved: () => void; // geometry changed: reload the scene
}): JSX.Element {
  const [data, setData] = useState<ReviewData | null>(null);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showAssumed, setShowAssumed] = useState(false);

  useEffect(() => {
    api.review(p.detailId).then(setData).catch((e: Error) => setErr(e.message));
  }, [p.detailId]);

  const save = async (body: Parameters<typeof api.saveReview>[1], geometry: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      setData(await api.saveReview(p.detailId, body));
      if (geometry) p.onSaved();
    } catch (e) {
      setErr((e as Error).message.replace(/^POST [^:]+: \d+ /, "").slice(0, 220));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <aside className="guide review">{err ? <div className="liberr">{err}</div> : "loading…"}</aside>;

  const printed = data.params.filter((x) => x.provenance === "drawing");
  const other = data.params.filter((x) => x.provenance !== "drawing" && x.used_by.length);
  const row = (q: ReviewParam) => (
    <div key={q.name} className={"rv" + (q.accepted ? " ok" : "")}>
      {/* the whole description shows the parts this value drives */}
      <div className="rvshow" onClick={() => q.used_by.length && p.onShow(q.used_by)} title="show the parts this drives">
        <div className="rvtop">
          <span className={badgeClass(q.provenance)}>{badgeLabel(q.provenance)}</span>
          <b>{q.name}</b>
          <span>
            = {q.value} {q.unit}
          </span>
          {q.kind === "min" && <span className="k">min</span>}
        </div>
        {q.source_quote && <div className="q">“{q.source_quote}”</div>}
        {q.edited && <div className="k">edited: was {q.edited.from}</div>}
        <div className="k">drives: {q.used_by.length ? q.used_by.join(", ") : "nothing"}</div>
      </div>
      <div className="rvact">
        <button disabled={busy || q.accepted} onClick={() => void save({ accept: [q.name] }, false)}>
          {q.accepted ? "✓ accepted" : "✓ accept"}
        </button>
        <input
          type="number"
          value={edit[q.name] ?? ""}
          placeholder={String(q.value)}
          onChange={(e) => setEdit({ ...edit, [q.name]: e.target.value })}
        />
        <button
          disabled={busy || edit[q.name] === undefined || edit[q.name] === ""}
          onClick={() =>
            void save({ edits: [{ name: q.name, value: Number(edit[q.name]) }], accept: [q.name] }, true).then(() =>
              setEdit({ ...edit, [q.name]: "" }),
            )
          }
        >
          fix
        </button>
      </div>
    </div>
  );

  return (
    <aside className="guide review" id="reviewPanel">
      <div className="ghead">
        <b>
          Review{" "}
          <span className={data.status === "reviewed" ? "badge b-draw" : "badge b-assump"}>
            {data.status === "reviewed" ? `reviewed by ${data.by}` : "draft"}
          </span>
        </b>
        <span>Check each value against the drawing. Fixes move every dimension computed from them.</span>
      </div>
      <div className="gmsgs">
        {err && <div className="liberr">{err}</div>}
        <h3>Printed on the drawing · {printed.length}</h3>
        {printed.length ? printed.map(row) : <div className="k">none confirmed</div>}
        <h3 className="toggle" onClick={() => setShowAssumed(!showAssumed)}>
          {showAssumed ? "▾" : "▸"} Assumed values · {other.length}
        </h3>
        {showAssumed && other.map(row)}
        <h3>Labels</h3>
        {data.parts
          .filter((x) => x.label || data.read_labels.length)
          .map((x) => (
            <div key={x.id} className="lbrow">
              <span onClick={() => p.onShow([x.id])}>{x.id}</span>
              <select
                value={x.label || ""}
                disabled={busy}
                onChange={(e) => void save({ labels: [{ part_id: x.id, label: e.target.value || null }] }, true)}
              >
                <option value="">— no label —</option>
                {data.read_labels.map((l) => (
                  <option key={l} value={l}>
                    {l.length > 48 ? l.slice(0, 46) + "…" : l}
                  </option>
                ))}
              </select>
            </div>
          ))}
        {(data.gate_notes.length > 0 || data.revisions.length > 0) && <h3>What the checks changed</h3>}
        {data.gate_notes.map((n, i) => (
          <div key={i} className="k">
            gate: {n}
          </div>
        ))}
        {data.revisions.map((r, i) => (
          <div key={i} className="k">
            self-check {i + 1}: {r.accepted ? "accepted" : r.reason} · {r.issues.length} issue(s)
          </div>
        ))}
      </div>
      <div className="gctl">
        <input placeholder="Reviewer name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
        <button
          id="publish"
          className="primary"
          disabled={busy || !reviewer.trim()}
          onClick={() => void save({ publish: true, reviewer }, true)}
        >
          Publish
        </button>
      </div>
    </aside>
  );
}
