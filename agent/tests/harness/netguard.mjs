// Network guard for Node child processes started by tests.
//
// spawn_offline_child launches node with `--import <file URI of this file>`,
// so this runs before the harness script. It makes every outbound network
// entry point throw and prints a marker so a test can prove it was loaded.

import dgram from "node:dgram";
import dns from "node:dns";
import http from "node:http";
import http2 from "node:http2";
import https from "node:https";
import net from "node:net";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";

const GUARD_MARKER = "advisor offline guard: guard loaded (node)";
const GUARD_ERROR_TEXT = "advisor offline guard: network access is blocked";

function blocked(what) {
  return function guarded() {
    throw new Error(`${GUARD_ERROR_TEXT} (${what})`);
  };
}

net.connect = blocked("net.connect");
net.createConnection = blocked("net.createConnection");
net.Socket.prototype.connect = blocked("net.Socket.prototype.connect");
tls.connect = blocked("tls.connect");
dns.lookup = blocked("dns.lookup");
dns.promises.lookup = blocked("dns.promises.lookup");
// Every resolver method (resolve, resolve4, resolveMx, reverse and the rest)
// on the module, on dns.promises and on both Resolver classes.
const RESOLVER_METHOD = /^(resolve|reverse)/;
for (const target of [dns, dns.promises, dns.Resolver.prototype, dns.promises.Resolver.prototype]) {
  for (const name of Object.getOwnPropertyNames(target)) {
    if (RESOLVER_METHOD.test(name) && typeof target[name] === "function") {
      target[name] = blocked(`dns ${name}`);
    }
  }
}
dgram.createSocket = blocked("dgram.createSocket");
http2.connect = blocked("http2.connect");
http.request = blocked("http.request");
http.get = blocked("http.get");
https.request = blocked("https.request");
https.get = blocked("https.get");

// fetch reports failure by rejecting, so callers using .catch see it too.
globalThis.fetch = function guardedFetch() {
  return Promise.reject(new Error(`${GUARD_ERROR_TEXT} (fetch)`));
};

// Named ESM imports of builtins (import { request } from "node:http") are
// separate bindings; this pushes the patched values into them.
syncBuiltinESMExports();

process.stderr.write(`${GUARD_MARKER}\n`);
