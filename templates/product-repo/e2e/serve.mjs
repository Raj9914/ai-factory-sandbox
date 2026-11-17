#!/usr/bin/env node
// Zero-dependency static server for the local Playwright run (playwright.config.ts webServer).
//
// Serves the local build in dist/ when `make build` produced dist/index.html, otherwise the
// example template page in e2e/site/. Binds to 127.0.0.1 only. GET and HEAD only; paths
// outside the served folder are refused.
//
// Usage: node e2e/serve.mjs     environment: E2E_PORT (default 4173; 0 picks a free port),
//                                E2E_ROOT (folder to serve; overrides the dist/ check)
import { createServer } from 'node:http';
import { existsSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const dist = resolve(here, '..', 'dist');
const root = resolve(process.env.E2E_ROOT
  || (existsSync(join(dist, 'index.html')) ? dist : join(here, 'site')));
const port = Number(process.env.E2E_PORT ?? 4173);

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.ico': 'image/x-icon', '.txt': 'text/plain; charset=utf-8',
  '.webmanifest': 'application/manifest+json', '.woff2': 'font/woff2',
};

function send(res, status, body, type = 'text/plain; charset=utf-8', head = false) {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
  res.end(head ? undefined : body);
}

async function resolveFile(urlPath) {
  let path;
  try {
    path = decodeURIComponent(urlPath.split(/[?#]/, 1)[0]);
  } catch {
    return null;
  }
  if (path.includes('\0')) return null;
  let file = resolve(root, '.' + path);
  if (file !== root && !file.startsWith(root + sep)) return null;
  try {
    if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
    return (await stat(file)).isFile() ? file : null;
  } catch {
    return null;
  }
}

const server = createServer(async (req, res) => {
  const head = req.method === 'HEAD';
  if (req.method !== 'GET' && !head) return send(res, 405, 'method not allowed\n');
  const file = await resolveFile(req.url || '/');
  if (!file) return send(res, 404, 'not found\n', undefined, head);
  let body;
  try {
    body = await readFile(file);
  } catch {
    return send(res, 500, 'read error\n', undefined, head);
  }
  send(res, 200, body, TYPES[extname(file).toLowerCase()] || 'application/octet-stream', head);
});

server.listen(port, '127.0.0.1', () => {
  console.log(`e2e: serving ${root} at http://127.0.0.1:${server.address().port}/`);
});
