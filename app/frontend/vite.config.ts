import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Built assets are served by FastAPI under /static/ui/ (hashed names, long cache);
// /app/* returns index.html for the client router. No inline scripts or styles: the
// production CSP is "script-src 'self'; style-src 'self'".
export default defineConfig({
  base: "/static/ui/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, assetsInlineLimit: 0, chunkSizeWarningLimit: 600 },
  server: {
    // Development: run NSM on :8000 and `npm run dev`; API and pages are proxied.
    proxy: { "/api": "http://127.0.0.1:8000", "/login": "http://127.0.0.1:8000", "/logout": "http://127.0.0.1:8000", "/static/ui-icons.svg": "http://127.0.0.1:8000" },
  },
  test: { environment: "jsdom", globals: false, setupFiles: [] },
});
