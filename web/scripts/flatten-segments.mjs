// Next 16.3.5 `output: "export"` writes per-segment prefetch payloads as
// out/repo/__next.repo/__PAGE__.txt, but the client requests
// out/repo/__next.repo.__PAGE__.txt (segment-value-encoding.js replaces every
// "/" with "."). On a static host every prefetch 404s and navigation falls
// back to a slower full fetch. Copy each nested payload to the flat name the
// client asks for; delete this script once Next writes them flat itself.
import fs from "node:fs";
import path from "node:path";

const OUT = path.resolve(import.meta.dirname, "..", "out");
let copied = 0;

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith("__next.")) flatten(full, dir, entry.name);
    else walk(full);
  }
}

function flatten(segmentDir, routeDir, prefix) {
  for (const entry of fs.readdirSync(segmentDir, { withFileTypes: true })) {
    const full = path.join(segmentDir, entry.name);
    const name = `${prefix}.${entry.name}`;
    if (entry.isDirectory()) flatten(full, routeDir, name);
    else {
      fs.copyFileSync(full, path.join(routeDir, name));
      copied++;
    }
  }
}

walk(OUT);
console.log(`flatten-segments: ${copied} prefetch payloads copied to their flat names`);
