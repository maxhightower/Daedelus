import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, boardApi } from "./api";
import type {
  Artifact,
  Binding,
  CanvasBoard,
  CanvasItem,
  Execution,
  Health,
  MediaSource,
  NodeType,
  Project,
  ResourceRef,
  Selection,
  TargetSelector,
  Workflow,
} from "./types";
import { refKey } from "./types";

/** Presentation-only history entry (board layout); artifact changes use revisions instead. */
type Snapshot = Pick<CanvasBoard, "items" | "connections">;

export type LinkFilter = Record<"reference" | "execution" | "dependency" | "annotation", boolean> & { selectionOnly: boolean };

interface StudioState {
  health: Health | null;
  nodeTypes: NodeType[];
  projects: Project[];
  project: Project | null;
  sources: MediaSource[];
  bindings: Binding[];
  artifacts: Artifact[];
  workflows: Workflow[];
  executions: Record<string, Execution>; // latest execution per workflow id
  selectProject: (id: string | null) => void;
  refresh: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  // boards
  boards: { id: string; name: string }[];
  board: CanvasBoard | null;
  openBoard: (id: string) => Promise<void>;
  reloadBoard: () => Promise<void>;
  updateBoard: (fn: (b: CanvasBoard) => CanvasBoard, opts?: { history?: boolean }) => void;
  updateItem: (id: string, patch: Partial<CanvasItem>, opts?: { history?: boolean }) => void;
  patchPresentation: (id: string, patch: Record<string, any>) => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  saveState: "saved" | "pending" | "saving" | "error";
  placeResource: (ref: ResourceRef, x?: number, y?: number, extra?: any) => Promise<CanvasItem | undefined>;
  itemsFor: (ref: ResourceRef) => CanvasItem[];
  // interaction levels
  selection: Selection;
  select: (s: Selection) => void;
  activeItemId: string | null;
  setActive: (id: string | null) => void;
  focusItemId: string | null;
  setFocusItem: (id: string | null) => void;
  linkFilter: LinkFilter;
  setLinkFilter: (f: LinkFilter) => void;
  locate: (itemId: string) => void;
  registerLocator: (fn: (itemId: string) => void) => void;
  // agent dock
  agentOpen: boolean;
  setAgentOpen: (v: boolean) => void;
  agentTarget: TargetSelector;
  // misc
  error: string | null;
  setError: (e: string | null) => void;
  notice: string | null;
  notify: (m: string) => void;
  run: <T>(p: Promise<T>, ok?: string) => Promise<T | undefined>;
  watchExecution: (workflowId: string) => void;
}

const Ctx = createContext<StudioState | null>(null);

export function useStudio(): StudioState {
  const c = useContext(Ctx);
  if (!c) throw new Error("StudioProvider missing");
  return c;
}

const lsGet = (k: string) => {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
};
const lsSet = (k: string, v: string) => {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* storage unavailable: per-viewer convenience only */
  }
};

export function StudioProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [nodeTypes, setNodeTypes] = useState<NodeType[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(() => lsGet("daedelus.project"));
  const [sources, setSources] = useState<MediaSource[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [executions, setExecutions] = useState<Record<string, Execution>>({});
  const [boards, setBoards] = useState<{ id: string; name: string }[]>([]);
  const [board, setBoard] = useState<CanvasBoard | null>(null);
  const [saveState, setSaveState] = useState<StudioState["saveState"]>("saved");
  const [selection, setSelection] = useState<Selection>({ kind: "none" });
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const [focusItemId, setFocusItemId] = useState<string | null>(null);
  const [linkFilter, setLinkFilter] = useState<LinkFilter>({ reference: true, execution: true, dependency: true, annotation: true, selectionOnly: false });
  const [agentOpen, setAgentOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const [histTick, setHistTick] = useState(0);
  const saveTimer = useRef<any>(null);
  const boardRef = useRef<CanvasBoard | null>(null);
  const serverRev = useRef<number | null>(null);
  const locator = useRef<(id: string) => void>(() => {});
  const watched = useRef<Set<string>>(new Set());

  boardRef.current = board;

  const notify = useCallback((m: string) => {
    setNotice(m);
    setTimeout(() => setNotice((cur) => (cur === m ? null : cur)), 4000);
  }, []);
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

  const refreshProjects = useCallback(async () => setProjects(await api.projects()), []);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const [s, b, a, w] = await Promise.all([api.sources(projectId), api.bindings(projectId), api.artifacts(projectId), api.workflows(projectId)]);
      setSources(s);
      setBindings(b);
      setArtifacts(a);
      setWorkflows(w);
    } catch (e: any) {
      setError(e.message);
    }
  }, [projectId]);

  // ---------------------------------------------------------------- boards
  const flush = useCallback(async () => {
    saveTimer.current = null;
    const b = boardRef.current;
    if (!b || !projectId) return;
    setSaveState("saving");
    try {
      const saved = await boardApi.save(projectId, { ...b, connections: b.connections.filter((c) => !c.derived) }, serverRev.current);
      serverRev.current = saved.revision;
      // keep local items (the user may have kept editing); take server-side derived data
      setBoard((cur) => (cur && cur.id === saved.id ? { ...cur, revision: saved.revision, connections: saved.connections, missing_items: saved.missing_items } : cur));
      setSaveState("saved");
    } catch (e: any) {
      setSaveState("error");
      if (e.status === 409) {
        const fresh = await boardApi.get(projectId, b.id);
        serverRev.current = fresh.revision;
        setBoard(fresh);
        notify("Board was changed elsewhere; reloaded the saved layout.");
      } else setError(`Board not saved: ${e.message}`);
    }
  }, [projectId, notify]);

  const scheduleSave = useCallback(() => {
    setSaveState("pending");
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(flush, 500);
  }, [flush]);

  const openBoard = useCallback(
    async (id: string) => {
      if (!projectId) return;
      clearTimeout(saveTimer.current);
      if (saveState === "pending") await flush();
      const b = await run(boardApi.get(projectId, id));
      if (!b) return;
      serverRev.current = b.revision;
      past.current = [];
      future.current = [];
      setHistTick((t) => t + 1);
      setBoard(b);
      setSelection({ kind: "none" });
      setActiveItemId(null);
      setFocusItemId(null);
      lsSet(`daedelus.board.${projectId}`, id);
    },
    [projectId, run, flush, saveState],
  );

  const reloadBoard = useCallback(async () => {
    const b = boardRef.current;
    if (!b || !projectId) return;
    const fresh = await boardApi.get(projectId, b.id).catch(() => null);
    if (!fresh) return;
    setBoard((cur) => {
      if (!cur || cur.id !== fresh.id) return cur;
      // never discard local unsaved layout: only adopt server items when nothing is pending
      if (fresh.revision !== serverRev.current && saveTimer.current === null) {
        serverRev.current = fresh.revision;
        return fresh;
      }
      return { ...cur, connections: fresh.connections, missing_items: fresh.missing_items };
    });
  }, [projectId]);

  const pushHistory = useCallback((b: CanvasBoard) => {
    past.current.push({ items: b.items, connections: b.connections });
    if (past.current.length > 100) past.current.shift();
    future.current = [];
    setHistTick((t) => t + 1);
  }, []);

  const updateBoard = useCallback(
    (fn: (b: CanvasBoard) => CanvasBoard, opts: { history?: boolean } = {}) => {
      const cur = boardRef.current;
      if (!cur) return;
      if (opts.history !== false) pushHistory(cur);
      const next = fn(cur);
      boardRef.current = next;
      setBoard(next);
      scheduleSave();
    },
    [pushHistory, scheduleSave],
  );

  const updateItem = useCallback(
    (id: string, patch: Partial<CanvasItem>, opts: { history?: boolean } = {}) =>
      updateBoard((b) => ({ ...b, items: b.items.map((i) => (i.id === id ? { ...i, ...patch } : i)) }), opts),
    [updateBoard],
  );

  const patchPresentation = useCallback(
    (id: string, patch: Record<string, any>) =>
      updateBoard(
        (b) => ({ ...b, items: b.items.map((i) => (i.id === id ? { ...i, presentation_state: { ...i.presentation_state, ...patch } } : i)) }),
        { history: false },
      ),
    [updateBoard],
  );

  const undo = useCallback(() => {
    const cur = boardRef.current;
    const prev = past.current.pop();
    if (!cur || !prev) return;
    future.current.push({ items: cur.items, connections: cur.connections });
    setBoard({ ...cur, ...prev });
    boardRef.current = { ...cur, ...prev };
    setHistTick((t) => t + 1);
    scheduleSave();
  }, [scheduleSave]);
  const redo = useCallback(() => {
    const cur = boardRef.current;
    const nxt = future.current.pop();
    if (!cur || !nxt) return;
    past.current.push({ items: cur.items, connections: cur.connections });
    setBoard({ ...cur, ...nxt });
    boardRef.current = { ...cur, ...nxt };
    setHistTick((t) => t + 1);
    scheduleSave();
  }, [scheduleSave]);

  const placeResource = useCallback(
    async (ref: ResourceRef, x?: number, y?: number, extra: any = {}) => {
      const b = boardRef.current;
      if (!b || !projectId) return undefined;
      clearTimeout(saveTimer.current);
      if (saveTimer.current) await flush();
      saveTimer.current = null;
      const r = await run(boardApi.place(projectId, b.id, ref, x, y, extra));
      if (!r) return undefined;
      pushHistory(boardRef.current!);
      serverRev.current = r.board.revision;
      setBoard(r.board);
      boardRef.current = r.board;
      return r.item as CanvasItem;
    },
    [projectId, run, flush, pushHistory],
  );

  const itemsFor = useCallback((ref: ResourceRef) => (board?.items ?? []).filter((i) => refKey(i.resource_ref) === refKey(ref)), [board]);

  // --------------------------------------------------------------- loading
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
    if (!projectId) {
      setBoard(null);
      setBoards([]);
      return;
    }
    lsSet("daedelus.project", projectId);
    (async () => {
      await refresh();
      const list = await run(boardApi.list(projectId)); // migrates V0 projects on first open
      if (!list) return;
      setBoards(list);
      const remembered = lsGet(`daedelus.board.${projectId}`);
      const target = list.find((b) => b.id === remembered)?.id ?? list[0]?.id;
      if (target) {
        const b = await run(boardApi.get(projectId, target));
        if (b) {
          serverRev.current = b.revision;
          past.current = [];
          future.current = [];
          setBoard(b);
        }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (projectId) boardApi.list(projectId).then(setBoards).catch(() => {});
  }, [projectId, board?.name, board?.id]);

  // latest execution per workflow; poll while anything runs, then refresh artifacts + board
  const pollExecutions = useCallback(async () => {
    if (!projectId) return false;
    const list = await api.executions(projectId).catch(() => []);
    const latest: Record<string, string> = {};
    for (const e of list) if (!latest[e.workflow_id]) latest[e.workflow_id] = e.id;
    const out: Record<string, Execution> = {};
    await Promise.all(
      Object.entries(latest).map(async ([wid, eid]) => {
        const e = await api.execution(projectId, eid).catch(() => null);
        if (e) out[wid] = e;
      }),
    );
    setExecutions(out);
    return Object.values(out).some((e) => ["pending", "running"].includes(e.status));
  }, [projectId]);

  const pollTimer = useRef<any>(null);
  const watchExecution = useCallback(
    (wid: string) => {
      watched.current.add(wid);
      clearTimeout(pollTimer.current);
      const tick = async () => {
        const running = await pollExecutions();
        if (running) pollTimer.current = setTimeout(tick, 1000);
        else {
          watched.current.clear();
          await refresh();
          await reloadBoard();
        }
      };
      tick();
    },
    [pollExecutions, refresh, reloadBoard],
  );
  useEffect(() => {
    if (projectId) pollExecutions();
  }, [projectId, pollExecutions]);

  const project = useMemo(() => projects.find((p) => p.id === projectId) ?? null, [projects, projectId]);
  useEffect(() => {
    if (projects.length && projectId && !projects.find((p) => p.id === projectId)) setProjectId(null);
  }, [projects, projectId]);

  const agentTarget: TargetSelector = useMemo(() => {
    if (selection.kind === "component") return { scope: "component", artifact_id: selection.artifactId, component_id: selection.componentId };
    if (selection.kind === "items" && selection.itemIds.length === 1) {
      const it = board?.items.find((i) => i.id === selection.itemIds[0]);
      if (it?.resource_ref.kind === "artifact") return { scope: "artifact", artifact_id: it.resource_ref.id };
    }
    return { scope: "project" };
  }, [selection, board]);

  void histTick;
  const value: StudioState = {
    health,
    nodeTypes,
    projects,
    project,
    sources,
    bindings,
    artifacts,
    workflows,
    executions,
    selectProject: (id) => {
      setProjectId(id);
      setSelection({ kind: "none" });
    },
    refresh,
    refreshProjects,
    boards,
    board,
    openBoard,
    reloadBoard,
    updateBoard,
    updateItem,
    patchPresentation,
    undo,
    redo,
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
    saveState,
    placeResource,
    itemsFor,
    selection,
    select: setSelection,
    activeItemId,
    setActive: setActiveItemId,
    focusItemId,
    setFocusItem: setFocusItemId,
    linkFilter,
    setLinkFilter,
    locate: (id) => locator.current(id),
    registerLocator: (fn) => (locator.current = fn),
    agentOpen,
    setAgentOpen,
    agentTarget,
    error,
    setError,
    notice,
    notify,
    run,
    watchExecution,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
