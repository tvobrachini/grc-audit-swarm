/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "path";

export default defineConfig(({ mode }) => {
  // Dev only: like the Compose nginx proxy, add the API token server-side so
  // requests the browser cannot put a header on (the EventSource behind the
  // live agent feed) are authenticated too.
  const token = loadEnv(mode, process.cwd(), "").VITE_API_AUTH_TOKEN;
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": resolve(__dirname, "./src") },
    },
    server: {
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      globals: false,
      css: false,
    },
  };
});
