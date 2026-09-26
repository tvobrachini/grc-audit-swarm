import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// `test.globals` is off (see vite.config.ts), so RTL's own afterEach-based
// auto-cleanup never registers; do it explicitly so each test starts from an
// empty document.
afterEach(() => {
  cleanup();
});
