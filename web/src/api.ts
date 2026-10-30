/* Typed API client. Endpoint shapes mirror app/main.py. */

import type {
  Build,
  ComponentGroup,
  DetailSpec,
  Doc,
  GenJob,
  LibraryEntry,
  ReviewData,
  Scene,
  SpecSummary,
} from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${init?.method || "GET"} ${url}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return (await r.json()) as T;
}

/* A review event on a database version (v3_api.write_review). `correct`
   makes a new version and returns its id; the others return the same id. */
export interface VersionReview {
  reviewer: string;
  action: "accept" | "reject" | "resolve" | "correct" | "activate_memory";
  data?: Record<string, unknown>;
}

const reviewVersion = (versionId: string, body: VersionReview): Promise<{ review_id: string; version_id: string }> =>
  req(`/api/versions/${encodeURIComponent(versionId)}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  uploadDocument: (file: File): Promise<Doc> => {
    const fd = new FormData();
    fd.append("file", file);
    return req<Doc>("/api/documents", { method: "POST", body: fd });
  },
  listSpecs: (): Promise<{ specs: SpecSummary[] }> => req("/api/specs"),
  getSpec: (detailId: string): Promise<DetailSpec> => req(`/api/specs/${detailId}`),
  patchSpec: (detailId: string, fix: { component_id: string; param?: string; value?: number; source_label?: string }): Promise<DetailSpec> =>
    req(`/api/specs/${detailId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fix),
    }),
  createBuild: (detailId: string, docId: string | null, useDisplay: boolean): Promise<Build> =>
    req("/api/builds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detail_id: detailId, doc_id: docId, use_display_thickness: useDisplay }),
    }),
  aiStatus: (): Promise<{ configured: boolean; model: string; public: boolean }> => req("/api/ai/status"),
  drawingUrl: (detailId: string): string => `/api/specs/${encodeURIComponent(detailId)}/drawing`,
  review: (detailId: string): Promise<ReviewData> => req(`/api/specs/${encodeURIComponent(detailId)}/review`),
  saveReview: (
    detailId: string,
    body: {
      edits?: { name: string; value: number }[];
      accept?: string[];
      labels?: { part_id: string; label: string | null }[];
      publish?: boolean;
      reviewer?: string;
    },
  ): Promise<ReviewData> =>
    req(`/api/specs/${encodeURIComponent(detailId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  locate: (detailId: string): Promise<GenJob> =>
    req(`/api/specs/${encodeURIComponent(detailId)}/locate`, { method: "POST" }),
  reviewVersion,
  /** A reviewer confirms inferred parts: a new version in which they are
      user_input (confidence cleared, inferred_by kept). Must target the
      detail's current version, or the server answers 409. */
  confirmInferred: (versionId: string, reviewer: string, partIds: string[]): Promise<{ review_id: string; version_id: string }> =>
    reviewVersion(versionId, { reviewer, action: "correct", data: { confirm_inferred: partIds } }),
  library: (): Promise<{ details: LibraryEntry[]; components: ComponentGroup[] }> => req("/api/library"),
  scene: (detailId: string): Promise<Scene> => req(`/api/specs/${encodeURIComponent(detailId)}/scene`),
  thumbUrl: (detailId: string): string => `/api/specs/${encodeURIComponent(detailId)}/thumb`,
  /** Library card image: a render of the 3D, never the source drawing. */
  previewUrl: (detailId: string): string => `/api/specs/${encodeURIComponent(detailId)}/preview`,
  /** False when there is no card image or it is older than the detail. */
  hasPreview: async (detailId: string): Promise<boolean> =>
    (await fetch(`/api/specs/${encodeURIComponent(detailId)}/preview`, { cache: "no-cache" })).ok,
  savePreview: async (detailId: string, jpegDataUrl: string): Promise<void> => {
    const body = await (await fetch(jpegDataUrl)).blob();
    await req(`/api/specs/${encodeURIComponent(detailId)}/preview`, {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body,
    });
  },
  guide: (detailId: string, question: string, history: { role: string; text: string }[]): Promise<GenJob> =>
    req("/api/guide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ detail_id: detailId, question, history }),
    }),
  /** Poll a background AI job until it settles. */
  waitJob: async (job: GenJob, onTick?: (j: GenJob) => void): Promise<GenJob> => {
    let g = job;
    while (g.state === "queued" || g.state === "running") {
      await new Promise((r) => setTimeout(r, 1000));
      g = await req<GenJob>(`/api/generate/${g.id}`);
      onTick?.(g);
    }
    return g;
  },
  generate: (docId: string, cropId: string): Promise<GenJob> =>
    req("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: docId, crop_id: cropId }),
    }),
  getGenerate: (id: string): Promise<GenJob> => req(`/api/generate/${id}`),
  /* Queued or running jobs on one detail, newest first. */
  activeJobs: (detailId: string): Promise<{ jobs: { id: string; kind: string; state: string }[] }> =>
    req(`/api/jobs?detail_id=${encodeURIComponent(detailId)}&active=1`),
  critique: (detailId: string): Promise<GenJob> =>
    req(`/api/specs/${detailId}/critique`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }),
  remodelPart: (detailId: string, partId: string): Promise<GenJob> =>
    req(`/api/specs/${detailId}/cad`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part_id: partId }),
    }),
  getBuild: (jobId: string): Promise<Build> => req(`/api/builds/${jobId}`),
  getScene: (jobId: string): Promise<Scene> => req(`/api/builds/${jobId}/artifacts/scene.json`),
  artifactUrl: (jobId: string, name: string): string => `/api/builds/${jobId}/artifacts/${name}`,
  figureUrl: (docId: string, cropId: string): string => `/api/documents/${docId}/figures/${cropId}`,
  errorText: async (jobId: string): Promise<string> => {
    const r = await fetch(`/api/builds/${jobId}/artifacts/error.txt`);
    return r.text();
  },
};
