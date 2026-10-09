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
    let timer: any;
    const load = (attempt: number) => {
      const p = revPending.get(revisionId) ?? api.revision(project.id, revisionId);
      revPending.set(revisionId, p);
      p.then((r) => {
        revCache.set(revisionId, r);
        revPending.delete(revisionId);
        if (alive) setRev(r);
      }).catch(() => {
        revPending.delete(revisionId);
        if (alive && attempt < 6) timer = setTimeout(() => load(attempt + 1), 400 * 2 ** attempt);
      });
    };
    load(0);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [project, revisionId]);
  return rev;
}

export function invalidateRevision(id: string) {
  revCache.delete(id);
}

export const useHeadRevision = (a?: Artifact | null) => {
  // useRevision keeps the previous revision while the next one loads; never hand a view a
  // revision of a different artifact (a reused node shows another artifact meanwhile)
  const rev = useRevision(a?.head_revision_id);
  return rev && a && rev.artifact_id !== a.id ? null : rev;
};

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

import type { CanvasItem } from "../types";
/** Bounds of board items in flow coordinates (independent of which nodes are mounted). */
export function itemBounds(items: CanvasItem[]) {
  if (!items.length) return null;
  const x0 = Math.min(...items.map((i) => i.position.x));
  const y0 = Math.min(...items.map((i) => i.position.y));
  const x1 = Math.max(...items.map((i) => i.position.x + i.size.width));
  const y1 = Math.max(...items.map((i) => i.position.y + i.size.height));
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** `at` when it is free, else the nearest free slot around it (used when placing new nodes). */
export function freeSpotAt(items: CanvasItem[], size: { width: number; height: number }, at: { x: number; y: number }, gap = 40) {
  const probe = { position: { x: Math.round(at.x), y: Math.round(at.y) }, size } as CanvasItem;
  const solid = items.filter((i) => i.item_type !== "frame" && i.item_type !== "workflow");
  const free = !solid.some((i) => at.x < i.position.x + i.size.width + gap / 2 && at.x + size.width + gap / 2 > i.position.x && at.y < i.position.y + i.size.height + gap / 2 && at.y + size.height + gap / 2 > i.position.y);
  return free ? probe.position : freeSpot(items, size, probe, gap);
}

/** First position near `near` where an item of `size` overlaps no other non-frame item. */
export function freeSpot(items: CanvasItem[], size: { width: number; height: number }, near: CanvasItem, gap = 40) {
  const solid = items.filter((i) => i.item_type !== "frame" && i.item_type !== "workflow");
  const hits = (x: number, y: number) =>
    solid.some((i) => x < i.position.x + i.size.width + gap / 2 && x + size.width + gap / 2 > i.position.x && y < i.position.y + i.size.height + gap / 2 && y + size.height + gap / 2 > i.position.y);
  for (let ring = 1; ring < 40; ring++) {
    const dx = (near.size.width + gap) * ring;
    const dy = (near.size.height + gap) * ring;
    const cands = [
      [near.position.x, near.position.y + dy],
      [near.position.x + dx, near.position.y],
      [near.position.x - dx, near.position.y],
      [near.position.x, near.position.y - dy],
    ];
    for (const [x, y] of cands) if (!hits(x, y)) return { x: Math.round(x), y: Math.round(y) };
  }
  return { x: near.position.x, y: near.position.y + near.size.height + gap };
}
