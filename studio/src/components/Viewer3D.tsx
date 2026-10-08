import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

export interface CameraState {
  position: [number, number, number];
  target: [number, number, number];
}

// --- WebGL budget -----------------------------------------------------------
// Browsers cap live WebGL contexts (~16). Views beyond the budget show the static render.
const MAX_LIVE = 6;
const live = new Set<string>();
export function acquireGL(key: string): boolean {
  if (live.has(key)) return true;
  if (live.size >= MAX_LIVE) return false;
  live.add(key);
  return true;
}
export function releaseGL(key: string) {
  live.delete(key);
}
export const liveViewerCount = () => live.size;

const PRESETS: Record<string, [number, number, number]> = {
  perspective: [0.9, 0.6, 0.9],
  front: [0, 0.15, 1.4],
  side: [1.4, 0.15, 0],
  top: [0.001, 1.5, 0.001],
};

/**
 * Interactive GLB view of an artifact revision.
 * - renders on demand (no idle render loop),
 * - camera is per view (props in, onCameraChange out),
 * - picking resolves the clicked mesh to the persistent `daedelus_id` carried in glTF extras.
 */
export function Viewer3D({
  url,
  camera,
  preset,
  interactive,
  selectedId,
  markedIds = [],
  onPick,
  onCameraChange,
  onIdsLoaded,
  automationKey,
}: {
  automationKey?: string;
  url: string;
  camera?: CameraState | null;
  preset?: string;
  interactive: boolean;
  selectedId?: string | null;
  markedIds?: string[];
  onPick?: (componentId: string | null) => void;
  onCameraChange?: (c: CameraState) => void;
  onIdsLoaded?: (ids: string[]) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const ctx = useRef<{
    renderer: THREE.WebGLRenderer;
    scene: THREE.Scene;
    cam: THREE.PerspectiveCamera;
    controls: OrbitControls;
    model: THREE.Object3D | null;
    render: () => void;
    byId: Map<string, THREE.Mesh[]>;
    originals: Map<THREE.Mesh, THREE.Material | THREE.Material[]>;
  } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const cb = useRef({ onPick, onCameraChange, onIdsLoaded });
  cb.current = { onPick, onCameraChange, onIdsLoaded };
  const camRef = useRef(camera);
  camRef.current = camera;

  // renderer lifecycle (once per mount)
  useEffect(() => {
    const el = host.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    } catch (e: any) {
      setErr(`WebGL unavailable: ${e.message}`);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    renderer.domElement.style.display = "block";
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2a2d33);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 2.0);
    sun.position.set(3, 5, 4);
    scene.add(sun);
    const cam = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
    const controls = new OrbitControls(cam, renderer.domElement);
    let raf = 0;
    const render = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        renderer.render(scene, cam);
      });
    };
    let camTimer: any;
    controls.addEventListener("change", () => {
      render();
      clearTimeout(camTimer);
      camTimer = setTimeout(() => {
        const finite = [...cam.position.toArray(), ...controls.target.toArray()].every(Number.isFinite);
        if (!finite) return; // never persist a degenerate camera (e.g. framing an empty scene)
        cb.current.onCameraChange?.({
          position: cam.position.toArray().map((v) => +v.toFixed(4)) as any,
          target: controls.target.toArray().map((v) => +v.toFixed(4)) as any,
        });
      }, 300);
    });
    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = `${w}px`;
      renderer.domElement.style.height = `${h}px`;
      cam.aspect = w / h;
      cam.updateProjectionMatrix();
      render();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    // picking: a click without drag selects the component under the pointer
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => (down = { x: e.clientX, y: e.clientY });
    const onUp = (e: PointerEvent) => {
      if (!down || !controls.enabled) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      down = null;
      if (moved > 4 || !ctx.current?.model) return;
      const r = renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, cam);
      const hit = ray.intersectObject(ctx.current.model, true)[0];
      let o: THREE.Object3D | null = hit?.object ?? null;
      while (o && !o.userData?.daedelus_id) o = o.parent;
      cb.current.onPick?.(o ? (o.userData.daedelus_id as string) : null);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);
    ctx.current = { renderer, scene, cam, controls, model: null, render, byId: new Map(), originals: new Map() };
    resize();
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(camTimer);
      ro.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      scene.traverse((o: any) => {
        o.geometry?.dispose?.();
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: any) => m?.dispose?.());
      });
      renderer.dispose();
      renderer.forceContextLoss();
      el.removeChild(renderer.domElement);
      ctx.current = null;
    };
  }, []);

  // (re)load the model when the revision changes; keep this view's camera
  useEffect(() => {
    const c = ctx.current;
    if (!c) return;
    setLoading(true);
    let cancelled = false;
    new GLTFLoader().load(
      url,
      (gltf) => {
        if (cancelled || !ctx.current) return;
        if (c.model) c.scene.remove(c.model);
        c.model = gltf.scene;
        c.scene.add(gltf.scene);
        c.byId.clear();
        c.originals.clear();
        const ids: string[] = [];
        gltf.scene.traverse((o) => {
          const id = o.userData?.daedelus_id as string | undefined;
          if (id) ids.push(id);
          if ((o as THREE.Mesh).isMesh) {
            let p: THREE.Object3D | null = o;
            while (p && !p.userData?.daedelus_id) p = p.parent;
            const owner = p?.userData?.daedelus_id as string | undefined;
            if (owner) c.byId.set(owner, [...(c.byId.get(owner) ?? []), o as THREE.Mesh]);
            c.originals.set(o as THREE.Mesh, (o as THREE.Mesh).material);
          }
        });
        cb.current.onIdsLoaded?.(ids);
        const saved = camRef.current;
        const validSaved =
          !!saved && [...(saved.position ?? []), ...(saved.target ?? [])].length === 6 && [...saved.position, ...saved.target].every((v) => typeof v === "number" && Number.isFinite(v));
        if (saved && validSaved) {
          c.cam.position.fromArray(saved.position);
          c.controls.target.fromArray(saved.target);
          c.controls.update();
        } else frame(preset ?? "perspective");
        setLoading(false);
        applyHighlight();
        c.render();
      },
      undefined,
      (e: any) => !cancelled && setErr(`Could not load model: ${e?.message ?? e}`),
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // Automation hook: project a component's centre to client coordinates so tests can click real
  // geometry (picking still goes through the same raycast as a user click).
  useEffect(() => {
    if (!automationKey) return;
    const w = window as any;
    w.__dd3d = w.__dd3d ?? {};
    w.__dd3d[automationKey] = {
      project: (id: string) => {
        const c = ctx.current;
        const meshes = c?.byId.get(id);
        if (!c || !meshes?.length) return null;
        const box = new THREE.Box3();
        meshes.forEach((m) => box.expandByObject(m));
        const v = box.getCenter(new THREE.Vector3()).project(c.cam);
        const r = c.renderer.domElement.getBoundingClientRect();
        return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
      },
      ids: () => [...(ctx.current?.byId.keys() ?? [])],
      loaded: () => !!ctx.current?.model,
    };
    return () => {
      delete w.__dd3d[automationKey];
    };
  }, [automationKey]);

  const frame = (p: string) => {
    const c = ctx.current;
    if (!c?.model) return;
    const box = new THREE.Box3().setFromObject(c.model);
    if (box.isEmpty()) return; // nothing to frame yet (e.g. an empty artifact)
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    const center = box.getCenter(new THREE.Vector3());
    const dir = new THREE.Vector3(...(PRESETS[p] ?? PRESETS.perspective)).normalize();
    const dist = (size / 2 / Math.tan((c.cam.fov * Math.PI) / 360)) * 1.05;
    c.cam.position.copy(center).add(dir.multiplyScalar(dist));
    c.controls.target.copy(center);
    c.controls.update();
    c.render();
  };
  // a preset request (camera cleared) reframes on the model's real bounds
  useEffect(() => {
    if (!camera) frame(preset ?? "perspective");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, camera == null]);

  const applyHighlight = () => {
    const c = ctx.current;
    if (!c) return;
    for (const [mesh, mat] of c.originals) mesh.material = mat;
    const tint = (ids: string[], color: number, intensity: number) => {
      for (const id of ids)
        for (const mesh of c.byId.get(id) ?? []) {
          const base = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
          const m = (base as THREE.MeshStandardMaterial).clone();
          if ("emissive" in m) {
            m.emissive = new THREE.Color(color);
            m.emissiveIntensity = intensity;
          }
          mesh.material = m;
        }
    };
    tint(markedIds.filter((i) => i !== selectedId), 0x3f6ca8, 0.35);
    if (selectedId) tint([selectedId], 0xffa31a, 0.75);
    c.render();
  };
  useEffect(applyHighlight, [selectedId, markedIds.join("|")]);

  useEffect(() => {
    const c = ctx.current;
    if (c) {
      c.controls.enabled = interactive;
      c.render();
    }
  }, [interactive]);

  // external camera changes (e.g. preset buttons) are applied when they differ
  useEffect(() => {
    const c = ctx.current;
    if (!c || !camera) return;
    if (![...(camera.position ?? []), ...(camera.target ?? [])].every((v) => typeof v === "number" && Number.isFinite(v))) return;
    const same = c.cam.position.toArray().every((v, i) => Math.abs(v - camera.position[i]) < 1e-3);
    if (!same) {
      c.cam.position.fromArray(camera.position);
      c.controls.target.fromArray(camera.target);
      c.controls.update();
      c.render();
    }
  }, [camera?.position.join(","), camera?.target.join(",")]);

  return (
    <div className="viewer3d" ref={host} data-interactive={interactive ? "1" : "0"}>
      {loading && !err && <div className="viewer-msg">loading model…</div>}
      {err && <div className="error-box">{err}</div>}
    </div>
  );
}

