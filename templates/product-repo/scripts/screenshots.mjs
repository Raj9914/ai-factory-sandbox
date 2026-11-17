#!/usr/bin/env node
/**
 * Capture preview screenshots at the viewports in design/screens.yml (issue #41).
 *
 * Usage: node scripts/screenshots.mjs <base-url> [design/screens.yml] [out-dir]
 *        (defaults: design/screens.yml and out/)
 *
 * For every screen with a `route`, opens <base-url><route> in headless Chromium at the screen's
 * viewport and saves a full-page PNG as <out-dir>/<id>-<viewport>.png. Writes
 * <out-dir>/screens.json (one entry per screen: route, viewport, Figma URL and node id, file,
 * status) which the preview workflow turns into a PR comment table with `buildComment`.
 * See the header of design/screens.yml for the file format.
 *
 * Needs Playwright, resolved from the current directory (the template has no runtime
 * dependencies, so install it pinned and without saving it):
 *   npm install --no-save --no-package-lock playwright@<version> && npx --no-install playwright install chromium
 *
 * Time limits: each screen (navigation + screenshot) stops after SCREENSHOT_TIMEOUT_MS
 * (default 30000), and the whole run after SCREENSHOT_BUDGET_MS (default 300000); screens
 * left when the budget runs out are marked failed, so a hanging preview cannot stall CI.
 *
 * Security: runs against the given preview URL only; routes must be same-origin paths. Each
 * screen uses a fresh browser context with no cookies, storage, auth headers or secrets.
 *
 * Exit codes: 0 all screens captured; 1 a screen failed (HTTP status >= 400, timeout or error);
 * 2 usage, spec or setup error (nothing captured).
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const MARKER = '<!-- ship48-screenshots -->';
export const DEFAULT_TIMEOUT_MS = 30000;
export const DEFAULT_BUDGET_MS = 300000;
const VIEWPORT_RE = /^(\d{1,5})x(\d{1,5})$/;
const MAX_VIEWPORT = 8192;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const NODE_ID_RE = /^\d+[:-]\d+$/;
const FIGMA_URL_RE = /^https:\/\/(?:www\.)?figma\.com\/(?:file|design|proto|board)\/[A-Za-z0-9]+(?:[/?#][^\s()<>[\]]*)?$/;

export class SpecError extends Error {}

// --- minimal YAML-subset parser (same subset as scripts/validate_design_records.py) -----------

function stripComment(line) {
  let quote = null;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === '#' && (i === 0 || line[i - 1] === ' ' || line[i - 1] === '\t')) {
      return line.slice(0, i).trimEnd();
    }
  }
  return line.trimEnd();
}

function scalar(text, lineno) {
  text = text.trim();
  if (text === '') return null;
  if (text[0] === '"' || text[0] === "'") {
    if (text.length < 2 || text[text.length - 1] !== text[0]) {
      throw new SpecError(`line ${lineno}: unterminated quoted string`);
    }
    return text.slice(1, -1);
  }
  if (text.startsWith('[')) {
    if (!text.endsWith(']')) throw new SpecError(`line ${lineno}: unterminated inline list`);
    const inner = text.slice(1, -1).trim();
    return inner ? inner.split(',').map((part) => scalar(part, lineno)) : [];
  }
  if ('{&*|>!'.includes(text[0])) {
    throw new SpecError(`line ${lineno}: unsupported YAML syntax '${text[0]}'`);
  }
  return text;
}

function splitKey(text) {
  const m = /^("[^"]*"|'[^']*'|[^:\s][^:]*?):(?:\s+(.*))?$/.exec(text);
  if (!m) return null;
  return [m[1].trim().replace(/^["']|["']$/g, ''), m[2] || ''];
}

function tokens(text) {
  const out = [];
  text.replace(/\r\n/g, '\n').split('\n').forEach((raw, index) => {
    const lineno = index + 1;
    const leading = raw.slice(0, raw.length - raw.trimStart().length);
    if (leading.includes('\t')) throw new SpecError(`line ${lineno}: tabs are not allowed for indentation`);
    const line = stripComment(raw);
    const stripped = line.trim();
    if (!stripped || stripped === '---' || stripped === '...') return;
    out.push([line.length - line.replace(/^ +/, '').length, stripped, lineno]);
  });
  return out;
}

const isItem = (text) => text === '-' || text.startsWith('- ');

function parseBlock(toks, pos, indent) {
  return isItem(toks[pos][1]) ? parseList(toks, pos, indent) : parseMap(toks, pos, indent);
}

function parseValue(toks, pos, parentIndent, rest, lineno) {
  if (rest.trim()) return [scalar(rest, lineno), pos];
  if (pos < toks.length && toks[pos][0] > parentIndent) return parseBlock(toks, pos, toks[pos][0]);
  if (pos < toks.length && toks[pos][0] === parentIndent && toks[pos][1].startsWith('-')) {
    return parseList(toks, pos, parentIndent);
  }
  return [null, pos];
}

function parseMap(toks, pos, indent) {
  const result = {};
  while (pos < toks.length) {
    const [ind, text, lineno] = toks[pos];
    if (ind < indent) break;
    if (ind > indent) throw new SpecError(`line ${lineno}: unexpected indentation`);
    if (text.startsWith('-')) break;
    const split = splitKey(text);
    if (!split) throw new SpecError(`line ${lineno}: expected 'key: value'`);
    const [key, rest] = split;
    if (Object.prototype.hasOwnProperty.call(result, key)) {
      throw new SpecError(`line ${lineno}: duplicate key '${key}'`);
    }
    [result[key], pos] = parseValue(toks, pos + 1, indent, rest, lineno);
  }
  return [result, pos];
}

function parseList(toks, pos, indent) {
  const result = [];
  while (pos < toks.length) {
    const [ind, text, lineno] = toks[pos];
    if (ind < indent || !isItem(text)) {
      if (ind > indent) throw new SpecError(`line ${lineno}: unexpected indentation`);
      break;
    }
    if (ind > indent) throw new SpecError(`line ${lineno}: unexpected indentation`);
    const body = text.slice(1).trimStart();
    if (!body) {
      let value;
      [value, pos] = parseValue(toks, pos + 1, indent, '', lineno);
      result.push(value);
      continue;
    }
    const itemIndent = ind + (text.length - body.length);
    if (splitKey(body) && !/^["'[]/.test(body)) {
      const sub = [[itemIndent, body, lineno]];
      let j = pos + 1;
      while (j < toks.length && toks[j][0] > ind) sub.push(toks[j++]);
      const [value, used] = parseMap(sub, 0, itemIndent);
      if (used !== sub.length) throw new SpecError(`line ${sub[used][2]}: unexpected indentation`);
      result.push(value);
      pos = j;
    } else {
      result.push(scalar(body, lineno));
      pos += 1;
    }
  }
  return [result, pos];
}

/** Parse the YAML subset used by design/screens.yml. Throws SpecError. */
export function parseYaml(text) {
  const toks = tokens(text);
  if (!toks.length) return {};
  if (toks[0][0] !== 0) throw new SpecError(`line ${toks[0][2]}: top level must not be indented`);
  const [value, pos] = parseBlock(toks, 0, 0);
  if (pos !== toks.length) throw new SpecError(`line ${toks[pos][2]}: unexpected content`);
  return value;
}

// --- spec ---------------------------------------------------------------------------------------

const isMapping = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/**
 * Turn parsed screens.yml data into capture jobs.
 * Returns { screens: [...jobs], skipped: [...entries without a route], errors: [...] }.
 */
export function loadSpec(data) {
  const errors = [];
  const screens = [];
  const skipped = [];
  if (!isMapping(data)) return { screens, skipped, errors: ['spec must be a mapping with viewports and screens'] };

  const viewports = {};
  if (!isMapping(data.viewports) || !Object.keys(data.viewports).length) {
    errors.push('viewports must map names to "<width>x<height>", e.g. mobile: 390x844');
  } else {
    for (const [name, value] of Object.entries(data.viewports)) {
      const m = typeof value === 'string' ? VIEWPORT_RE.exec(value) : null;
      const width = m ? Number(m[1]) : 0;
      const height = m ? Number(m[2]) : 0;
      if (!m || width < 1 || height < 1 || width > MAX_VIEWPORT || height > MAX_VIEWPORT) {
        errors.push(`viewport ${name}: must be "<width>x<height>" between 1 and ${MAX_VIEWPORT}, got ${JSON.stringify(value)}`);
      } else {
        viewports[name] = { width, height };
      }
    }
  }

  if (!Array.isArray(data.screens) || !data.screens.length) {
    errors.push('screens must be a non-empty list');
    return { screens, skipped, errors };
  }

  const files = new Set();
  data.screens.forEach((screen, index) => {
    if (!isMapping(screen)) {
      errors.push(`screen #${index + 1}: must be a mapping`);
      return;
    }
    const id = screen.id;
    const label = typeof id === 'string' && id ? `screen ${id}` : `screen #${index + 1}`;
    if (screen.route === undefined || screen.route === null || screen.route === '') {
      skipped.push({ id: typeof id === 'string' ? id : '', name: typeof screen.name === 'string' ? screen.name : '' });
      return;
    }
    const before = errors.length;
    if (typeof id !== 'string' || !ID_RE.test(id)) {
      errors.push(`${label}: id must use letters, digits, "-" or "_" (it names the PNG file)`);
    }
    const route = screen.route;
    if (typeof route !== 'string' || !route.startsWith('/') || route.startsWith('//')
        || /[\s\\]/.test(route)) {
      errors.push(`${label}: route must be a same-origin path starting with "/", got ${JSON.stringify(route)}`);
    }
    const viewport = screen.viewport;
    if (typeof viewport !== 'string' || !Object.prototype.hasOwnProperty.call(viewports, viewport)) {
      errors.push(`${label}: viewport must be one of [${Object.keys(viewports).join(', ')}], got ${JSON.stringify(viewport)}`);
    }
    const figmaUrl = screen.figma_url;
    if (figmaUrl !== undefined && figmaUrl !== null && (typeof figmaUrl !== 'string' || !FIGMA_URL_RE.test(figmaUrl))) {
      errors.push(`${label}: figma_url is not a Figma file/design URL: ${JSON.stringify(figmaUrl)}`);
    }
    const nodeId = screen.node_id;
    if (nodeId !== undefined && nodeId !== null && (typeof nodeId !== 'string' || !NODE_ID_RE.test(nodeId))) {
      errors.push(`${label}: node_id must look like "12:34", got ${JSON.stringify(nodeId)}`);
    }
    if (errors.length !== before) return;
    const file = `${id}-${viewport}.png`;
    if (files.has(file)) {
      errors.push(`${label}: duplicate screenshot ${file} (ids must be unique)`);
      return;
    }
    files.add(file);
    screens.push({
      id,
      name: typeof screen.name === 'string' ? screen.name : id,
      route,
      viewport,
      ...viewports[viewport],
      figma_url: figmaUrl || null,
      node_id: nodeId || null,
      file,
    });
  });
  if (!errors.length && !screens.length) errors.push('no screen has a route; nothing to capture');
  return { screens, skipped, errors };
}

/** Absolute URL of `route` under `baseUrl` (keeps any base path); throws if it leaves the origin. */
export function resolveUrl(baseUrl, route) {
  let base;
  try {
    base = new URL(baseUrl);
  } catch {
    throw new SpecError(`base URL is not a valid URL: ${baseUrl}`);
  }
  if (base.protocol !== 'http:' && base.protocol !== 'https:') {
    throw new SpecError(`base URL must start with http:// or https://, got ${baseUrl}`);
  }
  if (base.username || base.password) throw new SpecError('base URL must not contain credentials');
  base.search = '';
  base.hash = '';
  if (!base.pathname.endsWith('/')) base.pathname += '/';
  const url = new URL(route.replace(/^\/+/, ''), base);
  if (url.origin !== base.origin) throw new SpecError(`route ${route} leaves the preview origin`);
  return url.href;
}

// --- capture ------------------------------------------------------------------------------------

function withTimeout(promise, ms, what) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms} ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function errorText(error) {
  return String(error && error.message ? error.message : error).split('\n')[0].slice(0, 200);
}

/**
 * Capture every screen. `chromium` is Playwright's browser type (injectable for tests).
 * Returns the manifest written to <outDir>/screens.json.
 */
export async function capture({ baseUrl, specPath, outDir, chromium, timeoutMs = DEFAULT_TIMEOUT_MS,
  budgetMs = DEFAULT_BUDGET_MS, log = console.log }) {
  const spec = loadSpec(parseYaml(await readFile(specPath, 'utf8')));
  if (spec.errors.length) throw new SpecError(spec.errors.map((e) => `${specPath}: ${e}`).join('\n'));
  const jobs = spec.screens.map((screen) => ({ ...screen, url: resolveUrl(baseUrl, screen.route) }));
  await mkdir(outDir, { recursive: true });

  const deadline = Date.now() + budgetMs;
  const results = [];
  let browser = null;
  try {
    browser = await withTimeout(chromium.launch({ headless: true, timeout: timeoutMs }), timeoutMs, 'browser launch');
    for (const job of jobs) {
      const { url, ...entry } = job;
      const remaining = deadline - Date.now();
      if (remaining <= 0) {
        results.push({ ...entry, status: 'failed', error: `time budget of ${budgetMs} ms exhausted` });
        continue;
      }
      const limit = Math.min(timeoutMs, remaining);
      let context = null;
      try {
        await withTimeout((async () => {
          context = await browser.newContext({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1 });
          const page = await context.newPage();
          page.setDefaultTimeout(limit);
          const response = await page.goto(url, { waitUntil: 'load', timeout: limit });
          const status = response ? response.status() : 0;
          if (status < 200 || status >= 400) throw new Error(`GET ${job.route} returned HTTP ${status || 'no response'}`);
          await page.screenshot({ path: path.join(outDir, job.file), fullPage: true, animations: 'disabled', timeout: limit });
        })(), limit, `screen ${job.id}`);
        results.push({ ...entry, status: 'ok', error: null });
        log(`ok      ${job.file}  ${url}`);
      } catch (error) {
        results.push({ ...entry, status: 'failed', error: errorText(error) });
        log(`FAILED  ${job.file}  ${url}  ${errorText(error)}`);
      } finally {
        if (context) await withTimeout(context.close(), 5000, 'context close').catch(() => {});
      }
    }
  } finally {
    if (browser) await withTimeout(browser.close(), 5000, 'browser close').catch(() => {});
  }

  const manifest = {
    base_url: new URL(baseUrl).origin,
    screens: results,
    skipped: spec.skipped,
  };
  await writeFile(path.join(outDir, 'screens.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

// --- PR comment ---------------------------------------------------------------------------------

function cell(value) {
  return String(value ?? '').replace(/[\r\n]+/g, ' ').replace(/[|<>`[\]]/g, (ch) => `\\${ch}`);
}

/**
 * Markdown PR comment (starts with MARKER) with one row per screen: screenshot link
 * (the artifact holding the PNGs) and Figma frame link. `manifest` may be null when the
 * capture step did not produce one.
 */
export function buildComment(manifest, { artifactUrl = '', runUrl = '', sha = '' } = {}) {
  const lines = [MARKER, '### Preview screenshots'];
  if (!manifest || !Array.isArray(manifest.screens)) {
    lines.push('', 'No screenshots were captured; see the workflow run for the error.');
  } else {
    const ok = manifest.screens.filter((s) => s.status === 'ok').length;
    lines.push('', `${ok} of ${manifest.screens.length} screens captured from \`${cell(manifest.base_url)}\`.`
      + ' Compare each PNG with its Figma frame.', '',
    '| Screen | Route | Viewport | Screenshot | Figma frame |', '|---|---|---|---|---|');
    for (const s of manifest.screens) {
      const viewport = `${cell(s.viewport)} (${Number(s.width)}×${Number(s.height)})`;
      let shot;
      if (s.status !== 'ok') shot = `failed: ${cell(s.error)}`;
      else if (artifactUrl) shot = `[${cell(s.file)}](${artifactUrl})`;
      else shot = cell(s.file);
      const node = cell(s.node_id || 'frame');
      const figma = s.figma_url && FIGMA_URL_RE.test(s.figma_url) ? `[${node}](${s.figma_url})` : (s.node_id ? node : 'none');
      lines.push(`| ${cell(s.id)} ${cell(s.name)} | \`${cell(s.route)}\` | ${viewport} | ${shot} | ${figma} |`);
    }
    if (Array.isArray(manifest.skipped) && manifest.skipped.length) {
      lines.push('', `Not captured (no route): ${manifest.skipped.map((s) => cell(s.id || s.name)).join(', ')}`);
    }
  }
  const footer = [];
  if (artifactUrl) footer.push(`PNG artifact: ${artifactUrl}`);
  if (sha) footer.push(`SHA \`${cell(sha)}\``);
  if (runUrl) footer.push(`Run: ${runUrl}`);
  if (footer.length) lines.push('', footer.join(' · '));
  return `${lines.join('\n')}\n`;
}

// --- CLI ----------------------------------------------------------------------------------------

function loadChromium() {
  const requireFromCwd = createRequire(path.join(process.cwd(), 'noop.js'));
  for (const name of ['playwright', '@playwright/test']) {
    try {
      return requireFromCwd(name).chromium;
    } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND') throw error;
    }
  }
  throw new SpecError('Playwright is not installed in the current directory; run: npm install --no-save '
    + '--no-package-lock playwright@<version> && npx --no-install playwright install chromium');
}

function positiveInt(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === '') return fallback;
  if (!/^\d+$/.test(raw) || Number(raw) < 1) throw new SpecError(`${name} must be a positive integer (ms), got ${raw}`);
  return Number(raw);
}

export async function main(argv = process.argv.slice(2), env = process.env) {
  if (argv.length < 1 || argv.length > 3 || argv[0].startsWith('-')) {
    console.error('usage: node scripts/screenshots.mjs <base-url> [design/screens.yml] [out-dir]');
    return 2;
  }
  const [baseUrl, specPath = 'design/screens.yml', outDir = 'out'] = argv;
  try {
    resolveUrl(baseUrl, '/');
    const timeoutMs = positiveInt(env, 'SCREENSHOT_TIMEOUT_MS', DEFAULT_TIMEOUT_MS);
    const budgetMs = positiveInt(env, 'SCREENSHOT_BUDGET_MS', DEFAULT_BUDGET_MS);
    const chromium = loadChromium();
    const manifest = await capture({ baseUrl, specPath, outDir, chromium, timeoutMs, budgetMs });
    const failed = manifest.screens.filter((s) => s.status !== 'ok').length;
    console.log(`screenshots: ${manifest.screens.length - failed} captured, ${failed} failed -> ${outDir}`);
    return failed ? 1 : 0;
  } catch (error) {
    if (error instanceof SpecError || error.code === 'ENOENT') {
      console.error(`screenshots: ${error.message}`);
      return 2;
    }
    console.error(`screenshots: ${errorText(error)}`);
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exitCode = await main();
}
