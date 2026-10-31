/* Backend shapes. Mirror of app/spec.py + API responses; keep in sync. */

export type Provenance = "drawing" | "manufacturer_doc" | "user_input" | "assumption" | "inferred";
/* Part-level provenance: shown in the drawing, filled in by a model or an
   archetype slot, or an inferred part a reviewer confirmed. Absent = drawing. */
export type PartProvenance = "drawing" | "inferred" | "user_input";
export type ParamRole = "physical" | "illustrative" | "display";
export type CheckStatus = "PASS" | "FAIL" | "UNVERIFIED" | "N/A";
/* Verification outcome tier (verification_v3.prepare_verification, quality["outcome"]). */
export type Outcome = "verified" | "complete_with_inferred" | "incomplete";

/* Fields a part carries on Component, NativePart and the scene part JSON. All
   optional: scenes verified before the detail world model have none. */
export interface PartOrigin {
  class_id?: string | null; // ontology component class id, e.g. "door_jamb"
  provenance?: PartProvenance;
  confidence?: number | null; // 0..1 existence confidence; only meaningful when inferred; null = unmeasured
  inferred_by?: string | null; // "archetype:<assembly_id>@<ontology_version>:<slot_id>"
}

export interface Param {
  name: string;
  value: number;
  unit: string;
  provenance: Provenance;
  role: ParamRole;
  source_quote: string | null;
  tolerance_mm: number | null;
  note: string | null;
}

export interface SourceRegion {
  crop_id: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  kind: "indicative" | "sam_mask" | "human_box";
}

export interface Component extends PartOrigin {
  id: string;
  type: string;
  source_label: string | null;
  representation: "solid" | "sheet" | "coating" | "annotation" | "zone";
  generator: { id: string; version: string };
  params: Param[];
  product: { ref: string | null; system: string | null; note: string | null } | null;
  path: { x: number; y: number; provenance: Provenance; note: string | null }[];
  install_after: string[];
  source_regions: SourceRegion[];
  color: string;
  note: string | null;
}

export interface Relation {
  source: string;
  target: string;
  kind: string;
}

export interface Unresolved {
  id: string;
  description: string;
  impacts: string[];
  blocks_geometry: boolean;
}

export interface CheckResult {
  id: string;
  stage: "A" | "B" | "C";
  description: string;
  status: CheckStatus;
  expected: unknown;
  actual: unknown;
  tolerance: unknown;
  evidence: string | null;
  required?: boolean; // v3 checks only: a required non-PASS blocks completion
}

export interface DetailSpec {
  spec_version: string;
  detail_id: string;
  title: string;
  origin: string;
  source: {
    filename: string;
    sha256: string;
    page_index: number;
    crop_id: string;
    page_width_pt: number;
    page_height_pt: number;
    rotation_deg: number;
    coord_note: string;
  };
  units: "mm";
  context: Record<string, unknown>;
  components: Component[];
  relations: Relation[];
  unresolved: Unresolved[];
  verification: CheckResult[];
  assembly_class?: string | null; // NativeProgram.assembly_class: an ontology assembly id, e.g. "door_threshold"
}

export interface Figure {
  crop_id: string;
  detail_id?: string; // this drawing's AI detail (set by GET /api/documents/{id})
  page: number;
  width: number;
  height: number;
  kind: "embedded_image" | "rendered_page"; // how the PDF gave us this image
  dpi: number | null; // the true render resolution; null for an extracted image
}

export interface Doc {
  id: string;
  filename: string;
  sha256: string;
  pages: number;
  figures: Figure[];
  warnings: string[]; // pages the renderer failed on; empty when all went well
}

export interface SpecSummary {
  detail_id: string;
  title: string;
  crop_id: string;
  sha256: string;
  origin: string;
}

export interface GenJob {
  id: string;
  state: "queued" | "running" | "done" | "failed";
  progress: string[];
  detail_id: string | null;
  error: string | null;
  gate_notes: string[];
  version_id?: string | null;
  run_id?: string;
  native_state?: string;
  continued_by?: string | null; // the run that continues this one automatically
  result?: GuideResult | null;
}

export interface Build {
  id: string;
  detail_id: string;
  doc_id: string | null;
  state: "queued" | "running" | "done" | "failed";
  progress: string[];
  report: { counts: Record<string, number>; failed: string[]; verdict: string } | null;
  artifacts?: string[];
}

export interface Solid extends PartOrigin {
  id: string;
  name: string;
  source_label: string | null;
  placeholder?: "pending" | "failed";
  representation: string;
  layer: string;
  color: string;
  note: string | null;
  tris: number[];
  tri_count: number;
  bounds: number[] | null;
  params: Param[];
  product: Component["product"];
  install_after: string[];
  source_regions: SourceRegion[];
}

/* The verification row's quality dict (database.verifications.quality). */
export interface VerificationQuality {
  scene_artifact_id?: string;
  outcome?: Outcome | null; // absent or null: no tier was decided (e.g. FAILs with nothing inferred)
  inferred_parts?: string[];
}

export interface Scene {
  detail_id: string;
  title: string;
  units: string;
  viewer_scale: number;
  origin: string;
  source: DetailSpec["source"];
  context: Record<string, unknown>;
  install_order: string[];
  relations: Relation[];
  unresolved: Unresolved[];
  verification: CheckResult[];
  solids: Solid[];
  version_id?: string; // database versions only (GET /api/versions/{id}/scene)
  manifest_sha?: string;
  quality?: VerificationQuality; // the version's latest verification quality, when the server sends it
}

export interface LibraryEntry {
  detail_id: string;
  title: string;
  origin: string;
  made_by: "human" | "ai";
  situation: [string, string, string];
  kind: string;
  parts: number;
  drawing_values: number;
  labels: number;
  review: "draft" | "reviewed" | null;
  located: number;
  generating?: string | null; // a queued or running AI job on this detail
  components: { id: string; type: string; label: string | null }[];
}

export interface ComponentGroup {
  type: string;
  details: { detail_id: string; title: string; part_ids: string[] }[];
}

export interface GuideAction {
  type: "focus" | "isolate" | "show_all" | "explode" | "section" | "view" | "install_step" | "select";
  ids: string[];
  value: number;
  view: "" | "iso" | "front" | "top" | "side";
}

export interface GuideStep {
  say: string;
  actions: GuideAction[];
  ungrounded: (number | string)[]; // numbers, or ratio text like "1:100"
}

export interface GuideResult {
  steps: GuideStep[];
  follow_up: string;
  ungrounded: number;
  retried: boolean;
}

export interface ReviewParam {
  name: string;
  value: number;
  unit: string;
  provenance: string;
  source_quote: string | null;
  kind: string;
  note: string | null;
  used_by: string[];
  accepted: boolean;
  edited: { from: number; to: number } | null;
}

export interface ReviewData {
  detail_id: string;
  status: "draft" | "reviewed";
  by: string | null;
  at: string | null;
  params: ReviewParam[];
  parts: { id: string; type: string; label: string | null; shape: string }[];
  read_labels: string[];
  gate_notes: string[];
  revisions: { accepted: boolean; reason: string; issues: { part_id: string; problem: string; fix: string }[] }[];
}

export interface Region {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  c: number;
  prompt: string;
}
