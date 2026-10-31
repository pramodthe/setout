export interface MakeProject {
  id: string;
  title: string;
  active_version: number | null;
  created: string;
  updated: string;
}

export interface MakeMessage {
  id: string;
  role: string;
  text: string;
  attachments: string[];
  job_id: string | null;
  created: string;
}

export interface MakeVersion {
  n: number;
  parent_n: number | null;
  source: string;
  status: string;
  profile: string;
  model: string;
  created: string;
  error: string | null;
  has_render: boolean;
}

export interface MakeDetail {
  project: MakeProject;
  messages: MakeMessage[];
  versions: MakeVersion[];
}

export interface MakeModel {
  id: string;
  label: string;
  model: string;
  ready: boolean;
  caps: { vision: boolean; tools: boolean };
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`${init?.method || "GET"} ${url}: ${response.status} ${(await response.text()).slice(0, 200)}`);
  }
  return (await response.json()) as T;
}

export const makeApi = {
  models: (): Promise<MakeModel[]> => req("/api/make/settings/models"),
  createProject: (title?: string): Promise<{ id: string; title: string }> =>
    req("/api/make/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(title ? { title } : {}),
    }),
  project: (id: string): Promise<MakeDetail> => req(`/api/make/projects/${encodeURIComponent(id)}`),
  upload: (id: string, file: File): Promise<{ id: string }> => {
    const body = new FormData();
    body.append("file", file);
    return req(`/api/make/projects/${encodeURIComponent(id)}/attachments`, { method: "POST", body });
  },
  message: (
    id: string,
    payload: { text: string; attachments?: string[]; mode: "create" | "feedback"; profile?: string },
  ): Promise<{ job_id: string }> =>
    req(`/api/make/projects/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  glbUrl: (id: string, n: number): string =>
    `/api/make/projects/${encodeURIComponent(id)}/versions/${n}/scene.glb`,
  treeUrl: (id: string, n: number): string =>
    `/api/make/projects/${encodeURIComponent(id)}/versions/${n}/tree.json`,
  previewUrl: (id: string, n: number): string =>
    `/api/make/projects/${encodeURIComponent(id)}/versions/${n}/preview.png`,
  renderUrl: (id: string, n: number): string =>
    `/api/make/projects/${encodeURIComponent(id)}/versions/${n}/render.png`,
  render: (id: string, n: number, quality: "draft" | "high"): Promise<{ job_id: string }> =>
    req(`/api/make/projects/${encodeURIComponent(id)}/versions/${n}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quality }),
    }),
  tree: (id: string, n: number): Promise<import("./edits").TreeNode> =>
    req(`/api/make/projects/${encodeURIComponent(id)}/versions/${n}/tree.json`),
  overrides: (
    id: string,
    n: number,
    body: { overrides: { name: string; location?: number[]; rotation_deg?: number[]; dims?: Record<string, number> }[] },
  ): Promise<{ job_id: string }> =>
    req(`/api/make/projects/${encodeURIComponent(id)}/versions/${n}/overrides`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
