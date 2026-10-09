// Thin typed client for the Daedelus backend.
import type {
  Artifact,
  CanvasBoard,
  ResourceRef,
  Binding,
  Execution,
  ExecutionSummary,
  Health,
  MediaSource,
  NodeType,
  Project,
  ResolvedContext,
  Revision,
  ContextPackage,
  SourceAnalysis,
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

// Authentication (V2.1). Servers started with DAEDELUS_API_TOKENS require it.
//  - Browser on the same origin: the API token is exchanged ONCE for an HttpOnly session
//    cookie (POST /api/auth/session); state-changing requests carry the session's CSRF token.
//    The token is never stored and never put into a URL.
//  - Cross-origin (desktop shell talking to a remote server): the token is held in memory
//    only and sent as a bearer header; <img> and EventSource use short-lived, project-scoped
//    tickets instead of the token.
// A legacy ?token= in the page URL is consumed and removed from the address bar at once.
export type AuthMode = "none" | "session" | "bearer" | "required";
let authMode: AuthMode = "none";
let csrf: string | null = null;
let bearer: string | null = null; // memory only
const fileTickets: Record<string, { ticket: string; expires: number }> = {};
const sameOrigin = () => !base || new URL(base, window.location.href).origin === window.location.origin;

export const getAuthMode = () => authMode;

async function rawFetch(path: string, init: RequestInit = {}) {
  return fetch(`${base}${path}`, { credentials: sameOrigin() ? "same-origin" : "omit", ...init });
}

/** Determine whether the server needs authentication and establish a session if possible. */
export async function initAuth(): Promise<AuthMode> {
  let legacy: string | null = null;
  try {
    const url = new URL(window.location.href);
    legacy = url.searchParams.get("token");
    if (legacy) {
      url.searchParams.delete("token");
      window.history.replaceState(null, "", url.toString());
    }
    sessionStorage.removeItem("daedelus.token"); // V2 stored it here; never again
  } catch {
    /* no URL / storage */
  }
  const cfg = await rawFetch("/api/auth/config").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!cfg || !cfg.auth_required) return (authMode = "none");
  if (legacy) return login(legacy);
  const r = await rawFetch("/api/auth/session").catch(() => null);
  if (r && r.ok) {
    const j = await r.json();
    if (j.csrf) {
      csrf = j.csrf;
      return (authMode = "session");
    }
  }
  return (authMode = "required");
}

/** Exchange an API token for a session (same origin) or keep it in memory (cross origin). */
export async function login(token: string): Promise<AuthMode> {
  if (sameOrigin()) {
    const r = await rawFetch("/api/auth/session", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new ApiError(r.status, "the token was not accepted");
    csrf = (await r.json()).csrf;
    return (authMode = "session");
  }
  const r = await rawFetch("/api/auth/session", { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new ApiError(r.status, "the token was not accepted");
  bearer = token;
  return (authMode = "bearer");
}

export async function logout(): Promise<void> {
  if (authMode === "session") await req("DELETE", "/api/auth/session").catch(() => null);
  bearer = null;
  csrf = null;
  authMode = "required";
}

async function ticket(pid: string, purpose: "events" | "files") {
  return req<{ ticket: string; expires: number }>("POST", "/api/auth/ticket", { project_id: pid, purpose });
}

/** Bearer mode: keep a files ticket for the open project (images, previews, downloads). */
export async function ensureFileTicket(pid: string): Promise<void> {
  if (authMode !== "bearer") return;
  const cur = fileTickets[pid];
  if (cur && cur.expires * 1000 - Date.now() > 120_000) return;
  fileTickets[pid] = await ticket(pid, "files");
}

const tq = (pid: string, sep: string) =>
  authMode === "bearer" && fileTickets[pid] ? `${sep}ticket=${encodeURIComponent(fileTickets[pid].ticket)}` : "";

export const fileUrl = (pid: string, rel: string) => `${base}/api/projects/${pid}/files/${rel}${tq(pid, "?")}`;

/** URL for the project's event stream; in bearer mode it carries a fresh single-use ticket. */
export async function eventsUrl(pid: string, since?: number): Promise<string> {
  const qs: string[] = [];
  if (since != null) qs.push(`since=${since}`);
  if (authMode === "bearer") qs.push(`ticket=${encodeURIComponent((await ticket(pid, "events")).ticket)}`);
  return `${base}/api/projects/${pid}/events${qs.length ? "?" + qs.join("&") : ""}`;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: any): Promise<T> {
  const headers: Record<string, string> = {};
  if (authMode === "bearer" && bearer) headers.Authorization = `Bearer ${bearer}`;
  if (authMode === "session" && csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
  const init: RequestInit = { method, headers, credentials: sameOrigin() ? "same-origin" : "omit" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const r = await fetch(`${base}${path}`, init);
  if (r.status === 401 && authMode !== "none") authMode = "required";
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

/** Long-poll form of the event stream (V2.1) for proxies that buffer server-sent events. */
export function pollEvents(pid: string, since?: number) {
  const qs = since == null ? "" : `?since=${since}&wait_s=20`;
  return req<{ seq: number; events: { seq: number; kind: string }[] }>("GET", `/api/projects/${pid}/events/poll${qs}`);
}

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
  // semantic understanding (V1.1)
  analyze: (pid: string, sid: string, body: { provider: string; model?: string | null; segment?: any; force?: boolean }) =>
    req<SourceAnalysis>("POST", `${P(pid)}/sources/${sid}/analyze`, body),
  analyses: (pid: string, sid: string) => req<SourceAnalysis[]>("GET", `${P(pid)}/sources/${sid}/analyses`),
  agentContext: (pid: string, body: { artifact_id?: string | null; component_id?: string | null }) =>
    req<ContextPackage>("POST", `${P(pid)}/agent/context`, body),
  preview: (pid: string, wid: string, node_id: string, all_units = false) =>
    req<any>("POST", `${P(pid)}/workflows/${wid}/preview`, { node_id, all_units }),
  preflight: (pid: string, wid: string) => req<Preflight>("GET", `${P(pid)}/workflows/${wid}/preflight`),
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

// V1 boards and in-place editing
export const boardApi = {
  list: (pid: string) => req<{ id: string; name: string; revision: number; items: number; updated_at: string }[]>("GET", `${P(pid)}/boards`),
  create: (pid: string, name: string, layout: "empty" | "default" = "empty") => req<CanvasBoard>("POST", `${P(pid)}/boards`, { name, layout }),
  get: (pid: string, bid: string) => req<CanvasBoard>("GET", `${P(pid)}/boards/${bid}`),
  save: (pid: string, board: CanvasBoard, expected_revision: number | null) =>
    req<CanvasBoard>("PUT", `${P(pid)}/boards/${board.id}`, { board, expected_revision }),
  rename: (pid: string, bid: string, name: string) => req<CanvasBoard>("PATCH", `${P(pid)}/boards/${bid}`, { name }),
  remove: (pid: string, bid: string) => req<any>("DELETE", `${P(pid)}/boards/${bid}`),
  place: (pid: string, bid: string, resource_ref: ResourceRef, x?: number, y?: number, extra: any = {}) =>
    req<{ item: any; board: CanvasBoard }>("POST", `${P(pid)}/boards/${bid}/place`, { resource_ref, x, y, ...extra }),
  edit: (pid: string, aid: string, operations: any[], message: string, base_revision_id?: string | null) =>
    req<Revision>("POST", `${P(pid)}/artifacts/${aid}/edit`, { operations, message, base_revision_id }),
  glb: (pid: string, rid: string) => req<{ path: string }>("GET", `${P(pid)}/revisions/${rid}/glb`),
  operations: (pid: string, aid: string) => req<any[]>("GET", `${P(pid)}/artifacts/${aid}/operations`),
};

// V1.2 Office views, cross-artifact dependencies, connectors
export interface OfficeView {
  artifact_id: string;
  adapter: "spreadsheet" | "document" | "presentation";
  revision_id: string;
  revision_number: number;
  view: any;
  pages: string[];
  pdf?: string | null;
}
export interface DependencyEnd {
  artifact_id: string;
  component_id: string;
  artifact_name?: string;
  component_name?: string;
  kind?: string;
}
export interface DependencyStatus {
  id: string;
  route: string;
  source: DependencyEnd;
  target: DependencyEnd;
  status: "synced" | "stale" | "never_synced" | "missing_source" | "missing_target" | "needs_recalc";
  reason?: string;
  options?: Record<string, any>;
}
export const officeApi = {
  view: (pid: string, aid: string, revision_id?: string) =>
    req<OfficeView>("GET", `${P(pid)}/artifacts/${aid}/office${revision_id ? `?revision_id=${revision_id}` : ""}`),
  dependencies: (pid: string, artifact_id?: string) =>
    req<DependencyStatus[]>("GET", `${P(pid)}/dependencies${artifact_id ? `?artifact_id=${artifact_id}` : ""}`),
  addDependency: (pid: string, body: { source: DependencyEnd; target: DependencyEnd; target_kind?: string; options?: any; note?: string }) =>
    req<DependencyStatus>("POST", `${P(pid)}/dependencies`, body),
  removeDependency: (pid: string, id: string) => req<any>("DELETE", `${P(pid)}/dependencies/${id}`),
  sync: (pid: string, body: { dependency_ids?: string[]; target_artifact_ids?: string[]; force?: boolean }) =>
    req<{ revisions: { artifact_id: string; revision_id: string; revision_number: number; changed: string[] }[]; failed: any[]; skipped: any[]; status: DependencyStatus[] }>(
      "POST",
      `${P(pid)}/dependencies/sync`,
      body,
    ),
  connectors: () => req<{ name: string; title: string; configured: boolean; available: boolean; reason?: string; verification: string }[]>("GET", `/api/connectors`),
  fromSource: (pid: string, source_id: string, name?: string) => req<{ artifact: Artifact; revision: Revision }>("POST", `${P(pid)}/artifacts/from_source`, { source_id, name }),
};

// V2 distributed execution
export type ExecutionTarget = "automatic" | "local" | "cloud_cpu" | "cloud_gpu";
export interface JobStatus {
  id: string;
  state: string;
  attempt: number;
  max_attempts: number;
  worker_id?: string | null;
  progress: number;
  message: string;
  error?: string | null;
  created_at: string;
  updated_at: string;
  method: string;
  adapter: string;
  artifact_id?: string | null;
  requires: string;
}
export interface WorkerStatus {
  id: string;
  name: string;
  capabilities: string[];
  adapters: string[];
  alive: boolean;
  last_seen: number;
  version: string;
  host?: string;
  credential_kind?: string; // provisioned | enrolled (V2.1)
  deployment?: string; // process | container | hosted
  isolation?: { profile?: string; verified?: boolean; controls?: Record<string, boolean>; error?: string };
  peer?: string;
}
export interface Preflight {
  workflow_id: string;
  nodes: {
    node_id: string;
    type: string;
    label: string;
    execution?: { requested: string; adapters: Record<string, { resolved: string | null; reason?: string; error?: string; where?: string; worker?: string | null; isolation?: string; isolation_verified?: boolean | null }> };
    model?: { provider: string; model: string | null; live: boolean; available: boolean; detail: string; price_known: boolean | null; evaluate_revise_loop: boolean; max_iterations: number };
  }[];
  budget: Record<string, any>;
  live_models: boolean;
  remote_execution: boolean;
  spend_bound: string;
  needs_confirmation: boolean;
}
export interface ExecutionSettings {
  target: ExecutionTarget;
  targets: ExecutionTarget[];
  adapters: Record<string, { local: boolean; local_detail: string; cloud_cpu: boolean; cloud_gpu: boolean }>;
  cluster: { workers_enabled: boolean; workers: WorkerStatus[]; jobs?: Record<string, number> };
}
export const clusterApi = {
  settings: (pid: string) => req<ExecutionSettings>("GET", `${P(pid)}/execution`),
  setTarget: (pid: string, target: ExecutionTarget) => req<ExecutionSettings>("PUT", `${P(pid)}/execution`, { target }),
  jobs: (pid: string) => req<JobStatus[]>("GET", `${P(pid)}/jobs`),
  job: (pid: string, jid: string) => req<any>("GET", `${P(pid)}/jobs/${jid}`),
  cancel: (pid: string, jid: string) => req<JobStatus>("POST", `${P(pid)}/jobs/${jid}/cancel`),
};
