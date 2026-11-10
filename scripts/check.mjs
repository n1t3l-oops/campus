import { readFile } from "node:fs/promises";

const html = await readFile("index.html", "utf8");
const manifest = JSON.parse(await readFile("manifest.webmanifest", "utf8"));
const sw = await readFile("sw.js", "utf8");

if (!html.includes("<main") || !html.includes("</html>")) {
  throw new Error("index.html does not look like a complete HTML document");
}
if (!manifest.name || !manifest.start_url || !Array.isArray(manifest.icons)) {
  throw new Error("manifest.webmanifest is missing required PWA fields");
}
if (!sw.includes("addEventListener") || !sw.includes("fetch")) {
  throw new Error("sw.js does not contain the expected service-worker handlers");
}

const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());

if (scripts.length === 0) throw new Error("No inline JavaScript found in index.html");

for (let i = 0; i < scripts.length; i++) {
  try {
    new Function(scripts[i]);
  } catch (error) {
    throw new Error(`Inline script ${i + 1} has a syntax error: ${error.message}`);
  }
}
try {
  new Function(sw);
} catch (error) {
  throw new Error(`Service worker has a syntax error: ${error.message}`);
}

console.log(`Campus validation passed: ${scripts.length} inline script(s), manifest and service worker.`);
