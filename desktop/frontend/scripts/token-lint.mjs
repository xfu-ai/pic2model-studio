import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/features/", import.meta.url));
async function files(dir) {
  const entries = await readdir(dir, { withFileTypes: true }).catch(() => []);
  return (await Promise.all(entries.map((entry) => entry.isDirectory() ? files(join(dir, entry.name)) : [join(dir, entry.name)]))).flat();
}
const violations = [];
for (const file of await files(root)) {
  if (!file.endsWith(".css") && !file.endsWith(".tsx")) continue;
  const source = await readFile(file, "utf8");
  if (/#(?:[\da-f]{3}){1,2}\b|\b(?:margin|padding|gap):\s*\d+px/i.test(source)) violations.push(file);
}
if (violations.length) throw new Error(`Business UI must use design tokens: ${violations.join(", ")}`);
