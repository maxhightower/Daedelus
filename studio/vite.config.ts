import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (daedelus serve --port 8765) and is proxied.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 4000 },
  test: { environment: "jsdom" },
} as any);
