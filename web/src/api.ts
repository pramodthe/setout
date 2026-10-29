/* Typed API client. Endpoint shapes mirror app/main.py. */

import type {
  Build,
  ComponentGroup,
  DetailSpec,
  Doc,
  GenJob,
  LibraryEntry,
  Scene,
  SpecSummary,
} from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${init?.method || "GET"} ${url}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return (await r.json()) as T;
}

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
  aiStatus: (): Promise<{ configured: boolean; model: string }> => req("/api/ai/status"),
  library: (): Promise<{ details: LibraryEntry[]; components: ComponentGroup[] }> => req("/api/library"),
  scene: (detailId: string): Promise<Scene> => req(`/api/specs/${encodeURIComponent(detailId)}/scene`),
  thumbUrl: (detailId: string): string => `/api/specs/${encodeURIComponent(detailId)}/thumb`,
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
  critique: (detailId: string, renders: string[]): Promise<GenJob> =>
    req(`/api/specs/${detailId}/critique`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ renders }),
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
