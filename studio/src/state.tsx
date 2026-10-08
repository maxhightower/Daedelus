import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Artifact, Binding, Health, MediaSource, NodeType, Project, Workflow } from "./types";

export type View = "workspace" | "sources" | "workflow" | "artifacts" | "executions" | "agent";

interface StudioState {
  health: Health | null;
  nodeTypes: NodeType[];
  projects: Project[];
  project: Project | null;
  sources: MediaSource[];
  bindings: Binding[];
  artifacts: Artifact[];
  workflows: Workflow[];
  view: View;
  setView: (v: View) => void;
  selectProject: (id: string | null) => void;
  refresh: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  error: string | null;
  setError: (e: string | null) => void;
  notice: string | null;
  notify: (m: string) => void;
  focus: Record<string, string | undefined>;
  setFocus: (k: string, v: string | undefined) => void;
  run: <T>(p: Promise<T>, ok?: string) => Promise<T | undefined>;
}

const Ctx = createContext<StudioState | null>(null);

export function useStudio(): StudioState {
  const c = useContext(Ctx);
  if (!c) throw new Error("StudioProvider missing");
  return c;
}

export function StudioProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [nodeTypes, setNodeTypes] = useState<NodeType[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(() => {
    try {
      return localStorage.getItem("daedelus.project");
    } catch {
      return null;
    }
  });
  const [sources, setSources] = useState<MediaSource[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [view, setView] = useState<View>("workspace");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [focus, setFocusState] = useState<Record<string, string | undefined>>({});

  const notify = useCallback((m: string) => {
    setNotice(m);
    setTimeout(() => setNotice((cur) => (cur === m ? null : cur)), 4000);
  }, []);

  const refreshProjects = useCallback(async () => {
    const ps = await api.projects();
    setProjects(ps);
  }, []);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const [s, b, a, w] = await Promise.all([
        api.sources(projectId),
        api.bindings(projectId),
        api.artifacts(projectId),
        api.workflows(projectId),
      ]);
      setSources(s);
      setBindings(b);
      setArtifacts(a);
      setWorkflows(w);
    } catch (e: any) {
      setError(e.message);
    }
  }, [projectId]);

  useEffect(() => {
    (async () => {
      try {
        const [h, nt] = await Promise.all([api.health(), api.nodeTypes()]);
        setHealth(h);
        setNodeTypes(nt);
        await refreshProjects();
      } catch (e: any) {
        setError(`Backend unreachable: ${e.message}`);
      }
    })();
  }, [refreshProjects]);

  useEffect(() => {
    try {
      if (projectId) localStorage.setItem("daedelus.project", projectId);
    } catch {
      /* storage unavailable */
    }
    refresh();
  }, [projectId, refresh]);

  const project = useMemo(() => projects.find((p) => p.id === projectId) ?? null, [projects, projectId]);

  useEffect(() => {
    if (projects.length && projectId && !projects.find((p) => p.id === projectId)) setProjectId(null);
  }, [projects, projectId]);

  const run = useCallback(
    async <T,>(p: Promise<T>, ok?: string): Promise<T | undefined> => {
      try {
        const r = await p;
        if (ok) notify(ok);
        return r;
      } catch (e: any) {
        setError(e.message ?? String(e));
        return undefined;
      }
    },
    [notify],
  );

  const value: StudioState = {
    health,
    nodeTypes,
    projects,
    project,
    sources,
    bindings,
    artifacts,
    workflows,
    view,
    setView,
    selectProject: setProjectId,
    refresh,
    refreshProjects,
    error,
    setError,
    notice,
    notify,
    focus,
    setFocus: (k, v) => setFocusState((f) => ({ ...f, [k]: v })),
    run,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
