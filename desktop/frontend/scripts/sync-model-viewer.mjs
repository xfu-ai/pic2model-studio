import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageRoot = resolve(frontendRoot, "node_modules", "@google", "model-viewer");
const destination = resolve(
  frontendRoot,
  "..",
  "src-tauri",
  "resources",
  "model-viewer",
);

mkdirSync(destination, { recursive: true });
copyFileSync(
  resolve(packageRoot, "dist", "model-viewer.min.js"),
  resolve(destination, "model-viewer.min.js"),
);
copyFileSync(resolve(packageRoot, "LICENSE"), resolve(destination, "LICENSE.txt"));
