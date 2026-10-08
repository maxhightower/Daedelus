// Thin typed client for the Daedelus backend.
import type {
  Artifact,
  Binding,
  Execution,
  ExecutionSummary,
  Health,
  MediaSource,
  NodeType,
  Project,
  ResolvedContext,
  Revision,
  TargetSelector,
  Workflow,
} from "./types";

let base = "";

/** In the desktop app the backend runs as a sidecar on a dynamic port. */
export async function initApiBase(): Promise<string> {
  const w = window as any;
  if (w.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    for (let i = 0; i < 100; i++) {
      try {
        base = await invoke<string>("backend_url");
        const r = await fetch(`${base}/api/health`);
        if (r.ok) return base;
      } catch {
        /* backend still starting */
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    throw new Error("backend sidecar did not start");
  }
  const q = new URLSearchParams(window.location.search).get("api");
  if (q) base = q;
  return base;
}

export const fileUrl = (pid: string, rel: string) => `${base}/api/projects/${pid}/files/${rel}`;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: any): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as any)["Content-Type"] = "application/json";
  }
  const r = await fetch(`${base}${path}`, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json();
}

const P = (pid: string) => `/api/projects/${pid}`;

export const api = {
  health: () => req<Health>("GET", "/api/health"),
  nodeTypes: () => req<NodeType[]>("GET", "/api/node-types"),
  projects: () => req<Project[]>("GET", "/api/projects"),
  createProject: (name: string, description = "") => req<Project>("POST", "/api/projects", { name, description }),
  createDemo: (include_video = true) => req<any>("POST", `/api/projects/demo?include_video=${include_video}`),
  patchProject: (pid: string, body: any) => req<Project>("PATCH", P(pid), body),

  sources: (pid: string) => req<MediaSource[]>("GET", `${P(pid)}/sources`),
  source: (pid: string, sid: string) => req<MediaSource>("GET", `${P(pid)}/sources/${sid}`),
  upload: (pid: string, files: File[], mediaType?: string) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    if (mediaType) fd.append("media_type", mediaType);
    return req<MediaSource[]>("POST", `${P(pid)}/sources/upload`, fd);
  },
  addUrl: (pid: string, url: string, name?: string) => req<MediaSource>("POST", `${P(pid)}/sources/url`, { url, name }),
  addText: (pid: string, name: string, text: string) => req<MediaSource>("POST", `${P(pid)}/sources/text`, { name, text }),
  addPath: (pid: string, path: string) => req<MediaSource>("POST", `${P(pid)}/sources/path`, { path }),
  updateText: (pid: string, sid: string, text: string) => req<MediaSource>("PUT", `${P(pid)}/sources/${sid}/text`, { text }),
  reingest: (pid: string, sid: string) => req<MediaSource>("POST", `${P(pid)}/sources/${sid}/reingest`),
  deleteSource: (pid: string, sid: string) => req<any>("DELETE", `${P(pid)}/sources/${sid}`),

  bindings: (pid: string) => req<Binding[]>("GET", `${P(pid)}/bindings`),
  createBinding: (pid: string, b: Partial<Binding>) => req<Binding>("POST", `${P(pid)}/bindings`, b),
  patchBinding: (pid: string, bid: string, b: Partial<Binding>) => req<Binding>("PATCH", `${P(pid)}/bindings/${bid}`, b),
  deleteBinding: (pid: string, bid: string) => req<any>("DELETE", `${P(pid)}/bindings/${bid}`),
  bindingScope: (pid: string, bid: string) => req<any>("GET", `${P(pid)}/bindings/${bid}/scope`),

  artifacts: (pid: string) => req<Artifact[]>("GET", `${P(pid)}/artifacts`),
  createArtifact: (pid: string, body: any) => req<{ artifact: Artifact; revision: Revision }>("POST", `${P(pid)}/artifacts`, body),
  revisions: (pid: string, aid: string) => req<Revision[]>("GET", `${P(pid)}/artifacts/${aid}/revisions`),
  revision: (pid: string, rid: string) => req<Revision>("GET", `${P(pid)}/revisions/${rid}`),
  revisionFiles: (pid: string, rid: string) => req<string[]>("GET", `${P(pid)}/revisions/${rid}/files`),
  revisionFile: (pid: string, rid: string, path: string) =>
    req<{ path: string; text?: string; binary?: boolean }>("GET", `${P(pid)}/revisions/${rid}/files?path=${encodeURIComponent(path)}`),
  restore: (pid: string, aid: string, revision_id: string) => req<Revision>("POST", `${P(pid)}/artifacts/${aid}/restore`, { revision_id }),

  resolve: (pid: string, target: TargetSelector) => req<ResolvedContext>("POST", `${P(pid)}/resolve`, { target }),
  context: (pid: string, cid: string) => req<ResolvedContext>("GET", `${P(pid)}/contexts/${cid}`),

  workflows: (pid: string) => req<Workflow[]>("GET", `${P(pid)}/workflows`),
  workflow: (pid: string, wid: string, version?: number) =>
    req<Workflow>("GET", `${P(pid)}/workflows/${wid}${version ? `?version=${version}` : ""}`),
  versions: (pid: string, wid: string) => req<{ version: number; created_at: string }[]>("GET", `${P(pid)}/workflows/${wid}/versions`),
  createWorkflow: (pid: string, wf: Partial<Workflow>) => req<Workflow>("POST", `${P(pid)}/workflows`, wf),
  saveWorkflow: (pid: string, wf: Workflow) => req<{ workflow: Workflow; issues: any[] }>("PUT", `${P(pid)}/workflows/${wf.id}`, wf),
  validateWorkflow: (pid: string, wf: Workflow) => req<{ node_id: string; level: string; message: string }[]>("POST", `${P(pid)}/workflows/validate`, wf),
  exportWorkflow: (pid: string, wid: string) => req<any>("GET", `${P(pid)}/workflows/${wid}/export`),
  importWorkflow: (pid: string, doc: any) => req<{ workflow: Workflow; issues: any[] }>("POST", `${P(pid)}/workflows/import`, doc),
  impact: (pid: string, wid: string) => req<any>("GET", `${P(pid)}/workflows/${wid}/impact`),
  preview: (pid: string, wid: string, node_id: string, all_units = false) =>
    req<any>("POST", `${P(pid)}/workflows/${wid}/preview`, { node_id, all_units }),
  execute: (pid: string, wid: string, mode = "incremental", nodes?: string[]) =>
    req<Execution>("POST", `${P(pid)}/workflows/${wid}/execute`, { mode, nodes }),

  executions: (pid: string, wid?: string) => req<ExecutionSummary[]>("GET", `${P(pid)}/executions${wid ? `?workflow_id=${wid}` : ""}`),
  execution: (pid: string, eid: string) => req<Execution>("GET", `${P(pid)}/executions/${eid}`),
  decide: (pid: string, eid: string, node_id: string, approve: boolean, note = "") =>
    req<any>("POST", `${P(pid)}/executions/${eid}/decide`, { node_id, approve, note }),
  cancel: (pid: string, eid: string) => req<any>("POST", `${P(pid)}/executions/${eid}/cancel`),
  replay: (pid: string, eid: string) => req<any>("POST", `${P(pid)}/executions/${eid}/replay`),

  messages: (pid: string) => req<any[]>("GET", `${P(pid)}/agent/messages`),
  sendMessage: (pid: string, body: any) => req<any>("POST", `${P(pid)}/agent/messages`, body),
};
