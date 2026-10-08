import { useStore } from "@xyflow/react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { api } from "../api";
import { useStudio } from "../state";
import type { Artifact, Revision } from "../types";

// ---------------------------------------------------------------- revisions
const revCache = new Map<string, Revision>();
const revPending = new Map<string, Promise<Revision>>();

/** Revision documents are immutable, so a process-wide cache is safe. */
export function useRevision(revisionId?: string | null): Revision | null {
  const { project } = useStudio();
  const [rev, setRev] = useState<Revision | null>(revisionId ? revCache.get(revisionId) ?? null : null);
  useEffect(() => {
    if (!project || !revisionId) {
      setRev(null);
      return;
    }
    const hit = revCache.get(revisionId);
    if (hit) {
      setRev(hit);
      return;
    }
    let alive = true;
    const p = revPending.get(revisionId) ?? api.revision(project.id, revisionId);
    revPending.set(revisionId, p);
    p.then((r) => {
      revCache.set(revisionId, r);
      revPending.delete(revisionId);
      if (alive) setRev(r);
    }).catch(() => revPending.delete(revisionId));
    return () => {
      alive = false;
    };
  }, [project, revisionId]);
  return rev;
}

export function invalidateRevision(id: string) {
  revCache.delete(id);
}

export const useHeadRevision = (a?: Artifact | null) => useRevision(a?.head_revision_id);

// ---------------------------------------------------------------- level of detail
export type Lod = "far" | "medium" | "close";
export const lodFor = (zoom: number): Lod => (zoom < 0.38 ? "far" : zoom < 0.85 ? "medium" : "close");
export function useLod(): Lod {
  return useStore((s) => lodFor(s.transform[2]));
}
export const useZoom = () => useStore((s) => s.transform[2]);

// ---------------------------------------------------------------- unsaved code edits
// Shared between the in-place editor and Focus so switching presentation never loses edits.
type DraftMap = Record<string, { original: string; text: string; baseRevision: string | null }>;
let drafts: DraftMap = {};
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
export const draftKey = (artifactId: string, path: string) => `${artifactId}::${path}`;
export const codeDrafts = {
  get: (k: string) => drafts[k],
  set: (k: string, v: DraftMap[string]) => {
    drafts = { ...drafts, [k]: v };
    emit();
  },
  discard: (k: string) => {
    const { [k]: _, ...rest } = drafts;
    drafts = rest;
    emit();
  },
  dirtyFor: (artifactId: string) =>
    Object.entries(drafts).filter(([k, d]) => k.startsWith(`${artifactId}::`) && d.text !== d.original).map(([k]) => k.split("::")[1]),
};
export function useDrafts(): DraftMap {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => drafts,
  );
}
export const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 14)}`;
