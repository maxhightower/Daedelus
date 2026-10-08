import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

/** Interactive preview of a GLB exported from the artifact's native .blend file. */
export function ModelViewer({ url, height = 320 }: { url: string; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    } catch (e: any) {
      setErr(`WebGL unavailable: ${e.message}`);
      return;
    }
    const w = el.clientWidth || 480;
    renderer.setSize(w, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2a2d33);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 2.0);
    sun.position.set(3, -4, 5);
    scene.add(sun);
    const camera = new THREE.PerspectiveCamera(40, w / height, 0.01, 1000);
    camera.up.set(0, 1, 0);
    const controls = new OrbitControls(camera, renderer.domElement);
    let frame = 0;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    new GLTFLoader().load(
      url,
      (gltf) => {
        scene.add(gltf.scene);
        const box = new THREE.Box3().setFromObject(gltf.scene);
        const size = box.getSize(new THREE.Vector3()).length() || 1;
        const center = box.getCenter(new THREE.Vector3());
        camera.position.copy(center).add(new THREE.Vector3(size * 0.9, size * 0.6, size * 0.9));
        controls.target.copy(center);
        const grid = new THREE.GridHelper(size * 3, 20, 0x555555, 0x3a3a3a);
        grid.position.y = box.min.y;
        scene.add(grid);
        animate();
      },
      undefined,
      (e: any) => setErr(`Could not load model: ${e?.message ?? e}`),
    );
    return () => {
      cancelAnimationFrame(frame);
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, [url, height]);
  return (
    <div className="model-viewer" ref={ref} style={{ height }}>
      {err && <div className="error-box">{err}</div>}
    </div>
  );
}
