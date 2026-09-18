// SC1b harness: run v1's validateBrief over a list of payloads (PLAN 2.3, 6.2).
//
// Usage: node --import <netguard.mjs> v1_validate.mjs <payloads.json | -> [reasons.json]
//
// The payload file (or stdin with "-" or no argument) is a JSON array of
// {id, payload}. The reasons file is the Python side's message to reason code
// table: {"messages": {<v1 message>: <code>}, "invalid_status_prefix": <text>,
// "invalid_status": <code>, "type_error": <code>}. The table lives in
// agent/rules/v1_parity.py and is passed in, so the two sides cannot drift.
//
// v1 lib/brief-validate.ts is imported by absolute file URL from
// V1_BRIEFCASE_DIR (decision 32); Node strips its types. Nothing is copied.
// Output on stdout: a JSON array of
// {id, decision, reason_code, error_name, error_message, normalized}.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

function readInput(arg) {
  const text = !arg || arg === "-" ? readFileSync(0, "utf8") : readFileSync(arg, "utf8");
  return JSON.parse(text);
}

function reasonFor(error, table) {
  if (!table) return null;
  if (error instanceof TypeError) return table.type_error;
  const message = String(error?.message ?? error);
  if (message.startsWith(table.invalid_status_prefix)) return table.invalid_status;
  return Object.hasOwn(table.messages, message) ? table.messages[message] : null;
}

async function main() {
  const dir = process.env.V1_BRIEFCASE_DIR;
  if (!dir) {
    process.stderr.write("v1_validate: V1_BRIEFCASE_DIR is not set\n");
    process.exit(2);
  }
  const url = pathToFileURL(join(dir, "lib", "brief-validate.ts")).href;
  const { validateBrief } = await import(url);
  const payloads = readInput(process.argv[2]);
  const table = process.argv[3] ? JSON.parse(readFileSync(process.argv[3], "utf8")) : null;

  const rows = payloads.map(({ id, payload }) => {
    const brief = structuredClone(payload);
    try {
      validateBrief(brief);
      return { id, decision: "accept", reason_code: null, error_name: null, error_message: null,
               normalized: JSON.parse(JSON.stringify(brief ?? null)) };
    } catch (error) {
      return { id, decision: "reject", reason_code: reasonFor(error, table),
               error_name: error?.name ?? typeof error, error_message: String(error?.message ?? error),
               normalized: null };
    }
  });
  process.stdout.write(JSON.stringify(rows));
}

await main();
