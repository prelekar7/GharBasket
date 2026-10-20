#!/usr/bin/env node
// Contract-gap report: which API calls the two frontends make that backend/api/openapi.json does not have.
//
//   node scripts/contract-gaps.mjs            write docs/integration/contract-gaps.md (just contract-gaps)
//   node scripts/contract-gaps.mjs --check    write nothing; exit 1 on an unclassified gap or a stale entry
//   node scripts/contract-gaps.mjs --json     print the scan as JSON (for tooling)
//   ... --spec path/to/openapi.json           compare with another spec, e.g. an older one from git (use with --json)
//
// It reads, never runs, the frontends: a static scan of the call sites.
//   mobile   frontend/mobile/src/**/*.{js,jsx}: request( and openStream(; lib/payments.js is part of src
//   console  frontend/console/{app,lib,components}/**/*.{js,jsx}: apiGet|apiPost|apiPatch|apiPut|apiDelete|
//            apiAll|apiDownload|apiFirstEvent|publicGet|authFetch( and streamFile( (the PDF download wrapper)
// mocks/ and test files are skipped, and so are the console's wrapper internals (lib/api.js, api-pages.js,
// download.js), where the path is only a parameter. Routes under /v1/dev/ exist only on a developer machine
// and are not in the spec; they count as found.
//
// How a call is read: the first argument is a string, a template or a concatenation; each ${…} becomes {}
// (an identifier that is a single `const x = "…"` in the same file is replaced by its value); the query string
// is cut off. The method comes from the function name, authFetch's first argument, or `method:` in the options
// object (none: GET). Matching is strict: a literal segment meets a literal segment, a {} meets a {param}.
// Anything else is "unresolved" and listed, never guessed. In the console, a helper of the same file that hands its
// parameter to apiPost(path, …) (reconciliation's post(path, key)) stands for its callers' paths.
// A call is "guarded" when the screen copes with a 404: wrapped in orNotShipped( (console), or marked @proposal in a
// comment just above it (console) or in the JSDoc of its function (mobile).
//
// Classification of the gaps is hand-maintained in docs/integration/gaps.json (see its "_help").
// No dependencies; Node 22.

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const specArg = process.argv.indexOf("--spec");
const SPEC =
  specArg > 0 ? resolve(process.argv[specArg + 1] ?? "") : join(ROOT, "backend/api/openapi.json");
const GAPS = join(ROOT, "docs/integration/gaps.json");
const REPORT = join(ROOT, "docs/integration/contract-gaps.md");
const METHODS = ["get", "post", "put", "patch", "delete"];
const CLASSES = ["pending-pr", "fix-frontend", "fix-backend", "later", "drop"];

// ---------------------------------------------------------------------------------------------
// Source reading
// ---------------------------------------------------------------------------------------------

/** All .js/.jsx files under `dir`, minus mocks, tests and build output. */
function walk(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (
        ["node_modules", "mocks", "test", "tests", "__tests__", ".next", "generated"].includes(name)
      )
        continue;
      out.push(...walk(full));
    } else if (
      /\.jsx?$/.test(name) &&
      !/\.(test|spec)\.jsx?$/.test(name) &&
      name !== "routeTree.gen.js"
    ) {
      out.push(full);
    }
  }
  return out;
}

/** Index after the string or template literal that starts at `i` (a quote or backtick). */
function skipString(src, i) {
  const q = src[i];
  let j = i + 1;
  while (j < src.length) {
    const c = src[j];
    if (c === "\\") j += 2;
    else if (c === q) return j + 1;
    else if (q !== "`" && c === "\n")
      return j; // a stray apostrophe in JSX text must not eat the file
    else if (q === "`" && c === "$" && src[j + 1] === "{") j = skipBraces(src, j + 1);
    else j++;
  }
  return j;
}

/** Index after the `{ … }` that starts at `i`, skipping strings and comments inside. */
function skipBraces(src, i) {
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    const c = src[j];
    if (c === '"' || c === "'" || c === "`") j = skipString(src, j) - 1;
    else if (c === "/" && src[j + 1] === "/")
      j = src.indexOf("\n", j) - 1 < j ? src.length : src.indexOf("\n", j) - 1;
    else if (c === "/" && src[j + 1] === "*") j = src.indexOf("*/", j) + 1;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return j + 1;
  }
  return src.length;
}

/** The source with every comment blanked out (same length, newlines kept), so nothing in a comment is a call. */
function maskComments(src) {
  let out = "";
  for (let i = 0; i < src.length; ) {
    const c = src[i];
    if (c === '"' || c === "'" || c === "`") {
      const end = skipString(src, i);
      out += src.slice(i, end);
      i = end;
    } else if (c === "/" && src[i + 1] === "/") {
      let end = src.indexOf("\n", i);
      if (end < 0) end = src.length;
      out += " ".repeat(end - i);
      i = end;
    } else if (c === "/" && src[i + 1] === "*") {
      let end = src.indexOf("*/", i + 2);
      end = end < 0 ? src.length : end + 2;
      out += src.slice(i, end).replace(/[^\n]/g, " ");
      i = end;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

/** The arguments of the call whose "(" is at `open`: [{ text, start }], plus the index after ")". */
function readArgs(src, open) {
  const args = [];
  let depth = 0;
  let start = open + 1;
  for (let j = open + 1; j < src.length; j++) {
    const c = src[j];
    if (c === '"' || c === "'" || c === "`") j = skipString(src, j) - 1;
    else if (c === "(" || c === "[" || c === "{") depth++;
    else if (c === ")" || c === "]" || c === "}") {
      if (depth === 0) {
        if (src.slice(start, j).trim()) args.push({ text: src.slice(start, j).trim(), start });
        return { args, end: j + 1 };
      }
      depth--;
    } else if (c === "," && depth === 0) {
      args.push({ text: src.slice(start, j).trim(), start });
      start = j + 1;
    }
  }
  return { args, end: src.length };
}

/** Splits `text` at top-level occurrences of `sep` (one character), outside strings and brackets. */
function splitTop(text, sep) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let j = 0; j < text.length; j++) {
    const c = text[j];
    if (c === '"' || c === "'" || c === "`") j = skipString(text, j) - 1;
    else if (c === "(" || c === "[" || c === "{") depth++;
    else if (c === ")" || c === "]" || c === "}") depth--;
    else if (c === sep && depth === 0) {
      parts.push(text.slice(start, j));
      start = j + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

/** The value of a top-level key of an object literal `{ … }`: { value } | { spread: true } | null (absent). */
function objectProp(objText, name) {
  const t = objText.trim();
  if (!t.startsWith("{") || !t.endsWith("}")) return { opaque: true };
  const inner = t.slice(1, -1);
  let spread = false;
  for (const part of splitTop(inner, ",")) {
    const p = part.trim();
    if (!p) continue;
    if (p.startsWith("...")) {
      spread = true;
      continue;
    }
    const m = /^(?:["']?)([A-Za-z_$][\w$]*)(?:["']?)\s*(?::([\s\S]*))?$/.exec(p);
    if (m && m[1] === name) return { value: m[2] === undefined ? name : m[2].trim() };
  }
  return spread ? { spread: true } : null;
}

const stringLiteral = (text) => {
  const m = /^(["'])((?:[^\\\n]|\\.)*?)\1$/.exec(text.trim());
  return m ? m[2] : null;
};

// ---------------------------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------------------------

/**
 * Every `const name = "…"` (or template) of the file: { name, lit, index, top }. `top` marks a declaration at
 * column 0, in scope everywhere; the others are indented, so in scope only up to the next column-0 declaration.
 */
function collectConsts(masked) {
  const found = [];
  const re = /\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(["'`])/g;
  for (let m = re.exec(masked); m; m = re.exec(masked)) {
    const start = m.index + m[0].length - 1;
    const end = skipString(masked, start);
    const after = masked.slice(end).match(/^\s*(.)/)?.[1];
    if (after === "+" || after === "?" || after === "." || after === "[" || after === "(") continue;
    const lineStart = masked.lastIndexOf("\n", m.index) + 1;
    const head = masked.slice(lineStart, m.index);
    found.push({
      name: m[1],
      lit: masked.slice(start, end),
      index: m.index,
      top: head === "" || head === "export ",
    });
  }
  return found;
}

const TOP_DECL = /\n(?:export |async function |function |const |let |class )/g;

/** The constants a call at `at` can see, name -> literal source (null: defined in two ways, so not usable). */
function constsAt(all, masked, at) {
  const scope = new Map();
  for (const d of all) {
    let visible = d.top;
    if (!visible && d.index < at) {
      TOP_DECL.lastIndex = d.index;
      const next = TOP_DECL.exec(masked);
      visible = !next || next.index >= at;
    }
    if (!visible) continue;
    const prev = scope.get(d.name);
    if (prev === undefined || prev?.index === undefined || d.index > prev.index) {
      if (prev && prev.lit !== d.lit && prev.top && d.top)
        scope.set(d.name, { lit: null, index: d.index });
      else scope.set(d.name, { lit: d.lit, index: d.index });
    }
  }
  return new Map([...scope].map(([k, v]) => [k, v.lit]));
}

/** The path a string-like expression stands for, with {} for each variable part; null when it is not string-like. */
function evalPath(expr, defs, depth = 0) {
  if (depth > 4) return null;
  const parts = splitTop(expr, "+").map((p) => p.trim());
  const out = [];
  let literals = 0;
  for (const part of parts) {
    const piece = evalPiece(part, defs, depth);
    if (piece === null) {
      out.push("{}");
    } else {
      out.push(piece);
      literals++;
    }
  }
  return literals ? out.join("") : null;
}

function evalPiece(part, defs, depth) {
  const q = part[0];
  if (q === '"' || q === "'") {
    return skipString(part, 0) === part.length ? stringLiteral(part) : null;
  }
  if (q === "`" && skipString(part, 0) === part.length) {
    const body = part.slice(1, -1);
    let out = "";
    for (let i = 0; i < body.length; ) {
      if (body[i] === "$" && body[i + 1] === "{") {
        const end = skipBraces(body, i + 1);
        const inner = body.slice(i + 2, end - 1).trim();
        let value = null;
        if (/^[A-Za-z_$][\w$]*$/.test(inner) && defs.get(inner))
          value = evalPath(defs.get(inner), defs, depth + 1);
        out += value ?? "{}";
        i = end;
      } else {
        out += body[i++];
      }
    }
    return out;
  }
  if (/^[A-Za-z_$][\w$]*$/.test(part) && defs.get(part))
    return evalPath(defs.get(part), defs, depth + 1);
  return null;
}

/** "/v1/…" with the query cut off and {} for variable parts; null when it is not an API path. */
function normalisePath(raw) {
  if (raw === null) return null;
  let p = raw.split("?")[0].replace(/\/{2,}/g, "/");
  p = p.replace(/([A-Za-z0-9_-])\{\}$/, "$1"); // "/v1/assignments${query}": a query appended to the path
  if (p.length > 1) p = p.replace(/\/$/, "");
  return p === "/v1" || p.startsWith("/v1/") ? p : null;
}

// ---------------------------------------------------------------------------------------------
// Spec
// ---------------------------------------------------------------------------------------------

function loadSpec() {
  const spec = JSON.parse(readFileSync(SPEC, "utf8"));
  const entries = Object.entries(spec.paths).map(([path, item]) => ({
    path,
    segs: path.split("/"),
    methods: METHODS.filter((m) => item[m]).map((m) => m.toUpperCase()),
  }));
  return { entries, operations: entries.reduce((n, e) => n + e.methods.length, 0) };
}

/**
 * How a call path meets the spec. strict: a literal meets the same literal, a {} meets a {param}.
 * wild: as strict, but a {} may also stand for a fixed word of the spec (the call has a variable where
 * the spec has a word). near: a literal may also hit a {param} (e.g. /current vs /{id}); a hint only.
 */
function matchPath(callPath, entries) {
  const segs = callPath.split("/");
  const strict = [];
  const wild = [];
  const near = [];
  for (const e of entries) {
    if (e.segs.length !== segs.length) continue;
    let isStrict = true;
    let isWild = true;
    let isNear = true;
    for (let i = 0; i < segs.length; i++) {
      const c = segs[i];
      const s = e.segs[i];
      const sParam = s.startsWith("{");
      if (c === "{}") {
        if (!sParam) isStrict = false;
      } else if (c.includes("{}")) {
        isStrict = false;
        if (!sParam && !new RegExp(`^${c.split("{}").map(escapeRe).join(".*")}$`).test(s))
          isWild = isNear = false;
      } else if (sParam) {
        isStrict = isWild = false;
      } else if (c !== s) {
        isStrict = isWild = isNear = false;
      }
    }
    if (isStrict) strict.push(e);
    else if (isWild) wild.push(e);
    else if (isNear) near.push(e);
  }
  return { strict, wild, near };
}
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// ---------------------------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------------------------

const CONSOLE_FN = {
  apiGet: "GET",
  apiAll: "GET",
  apiDownload: "GET",
  apiFirstEvent: "GET",
  publicGet: "GET",
  streamFile: "GET",
  apiPost: "POST",
  apiPatch: "PATCH",
  apiPut: "PUT",
  apiDelete: "DELETE",
  authFetch: null, // method is the first argument
};
const CONSOLE_INTERNALS = new Set(["lib/api.js", "lib/api-pages.js", "lib/download.js"]);

/** Whether a call is marked @proposal: in the JSDoc above its function (mobile) or in the comment lines just above it. */
function hasProposalMarker(lines, lineNo, app) {
  const at = (n) => lines[n - 1] ?? "";
  if ([lineNo, lineNo - 1, lineNo - 2, lineNo - 3].some((n) => at(n).includes("@proposal")))
    return true;
  if (app !== "mobile") return false;
  // the nearest line at column 0 that starts a declaration, then the comment block just above it
  let d = lineNo - 1;
  while (d > 0 && !/^(export |async function |function |const |let |class )/.test(at(d))) d--;
  let k = d - 1;
  while (k > 0 && /^\s*(\/\*\*|\*|\/\/|\*\/)/.test(at(k))) k--;
  return lines.slice(k, d - 1).some((l) => l.includes("@proposal"));
}

/** Name and parameter list of the column-0 function that encloses `index`, or null. */
function enclosingFunction(masked, index) {
  const re = /^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)/gm;
  let found = null;
  for (let m = re.exec(masked); m && m.index < index; m = re.exec(masked)) {
    found = { name: m[1], params: m[2].split(",").map((p) => p.trim().replace(/\s*=.*$/, "")) };
  }
  return found;
}

/** One call: its method, path and flags, or the reason it cannot be read. `pathArg`: the argument that holds the path. */
function readCall({ app, fn, args, pathArg, methodArg, defs, rawLines, masked, index, file }) {
  const line = masked.slice(0, index).split("\n").length;
  const call = { app, file, line, fn, flags: [] };
  if (!pathArg) return { ...call, unresolved: "no path argument" };
  let method;
  if (app === "mobile") {
    if (fn === "openStream" || !args[1]) method = "GET";
    else {
      const prop = objectProp(args[1].text, "method");
      if (prop === null) method = "GET";
      else if (prop.opaque)
        call.unresolved = `options are not an object literal: ${args[1].text.slice(0, 40)}`;
      else if (prop.spread) call.unresolved = "method may come from a spread";
      else {
        const lit = stringLiteral(prop.value ?? "");
        if (lit) method = lit.toUpperCase();
        else call.unresolved = `method is ${String(prop.value).slice(0, 40)}`;
      }
    }
  } else if (fn === "authFetch") {
    const lit = stringLiteral(methodArg.text);
    if (lit) method = lit.toUpperCase();
    else call.unresolved = `method is ${methodArg.text.slice(0, 40)}`;
  } else {
    method = CONSOLE_FN[fn];
  }
  const path = normalisePath(evalPath(pathArg.text, defs));
  if (path === null)
    call.unresolved ??= `path is ${pathArg.text.replace(/\s+/g, " ").slice(0, 60)}`;
  if (call.unresolved) return call;
  call.method = method;
  call.path = path;
  if (hasProposalMarker(rawLines, line, app)) call.flags.push("proposal");
  if (app === "console" && /orNotShipped\(\s*$/.test(masked.slice(Math.max(0, index - 80), index)))
    call.flags.push("notShipped");
  return call;
}

function scanFile(file, app, home) {
  const raw = readFileSync(file, "utf8");
  const masked = maskComments(raw);
  const rawLines = raw.split("\n");
  const allConsts = collectConsts(masked);
  const where = `frontend/${app}/${relative(home, file)}`;
  const names = app === "mobile" ? ["request", "openStream"] : Object.keys(CONSOLE_FN);
  const callRe = (list) =>
    new RegExp(`(?<![.\\w$])(?<!function\\s)(${list.join("|")})\\s*\\(`, "g");
  const calls = [];
  /** @type {Map<string, { fn: string, idx: number }>} functions of this file that pass a parameter on as the path */
  const wrappers = new Map();
  const re = callRe(names);
  for (let m = re.exec(masked); m; m = re.exec(masked)) {
    const fn = m[1];
    const { args } = readArgs(masked, m.index + m[0].length - 1);
    const ctx = {
      app,
      fn,
      args,
      masked,
      rawLines,
      file: where,
      index: m.index,
      pathArg: fn === "authFetch" ? args[1] : args[0],
      methodArg: fn === "authFetch" ? args[0] : null,
      defs: constsAt(allConsts, masked, m.index),
    };
    const call = readCall(ctx);
    // A helper like `post(path, key)` that hands its parameter to apiPost: its callers are the call sites.
    const ident = ctx.pathArg?.text;
    const host =
      call.unresolved && ident && /^[A-Za-z_$][\w$]*$/.test(ident)
        ? enclosingFunction(masked, m.index)
        : null;
    if (host?.params.includes(ident) && app === "console" && fn !== "authFetch") {
      wrappers.set(host.name, { fn, idx: host.params.indexOf(ident) });
      continue;
    }
    calls.push(call);
  }
  if (wrappers.size) {
    const wre = callRe([...wrappers.keys()]);
    for (let m = wre.exec(masked); m; m = wre.exec(masked)) {
      const { fn, idx } = wrappers.get(m[1]);
      const { args } = readArgs(masked, m.index + m[0].length - 1);
      calls.push(
        readCall({
          app,
          fn,
          args,
          masked,
          rawLines,
          file: where,
          index: m.index,
          pathArg: args[idx],
          methodArg: null,
          defs: constsAt(allConsts, masked, m.index),
        }),
      );
      calls[calls.length - 1].via = m[1];
    }
  }
  return calls;
}

function scanApp(app, spec) {
  const home = join(ROOT, "frontend", app);
  const dirs =
    app === "mobile" ? [join(home, "src")] : ["app", "lib", "components"].map((d) => join(home, d));
  const calls = [];
  for (const file of dirs.flatMap(walk)) {
    if (app === "console" && CONSOLE_INTERNALS.has(relative(home, file))) continue;
    calls.push(...scanFile(file, app, home));
  }
  for (const c of calls) {
    if (c.unresolved) {
      c.status = "unresolved";
      continue;
    }
    const { strict, wild, near } = matchPath(c.path, spec.entries);
    const show = (list) => list.map((e) => `${e.methods.join("/")} ${e.path}`).join(", ");
    c.key = `${c.method} ${c.path}`;
    if (c.path.startsWith("/v1/dev/")) {
      c.status = "found"; // developer-machine routes are kept out of openapi.json on purpose (include_in_schema=False)
    } else if (strict.some((e) => e.methods.includes(c.method))) {
      c.status = "found";
    } else if (strict.length) {
      c.status = "wrong-method";
      c.spec = show(strict);
    } else if (wild.length) {
      c.status = "unresolved";
      c.unresolved = `a variable stands where the spec has a fixed word: ${c.path} (could be ${show(wild)})`;
    } else {
      c.status = "missing";
      if (near.length) c.spec = `The spec has ${show(near)}.`;
    }
  }
  return calls;
}

// ---------------------------------------------------------------------------------------------
// Gaps, classification, report
// ---------------------------------------------------------------------------------------------

function loadGaps() {
  if (!existsSync(GAPS)) return { entries: {}, extraFixes: [] };
  const json = JSON.parse(readFileSync(GAPS, "utf8"));
  return { entries: json.entries ?? {}, extraFixes: json.extra_fixes ?? [] };
}

/** Groups the gap calls (missing, wrong method) by "METHOD /path". */
function groupGaps(calls) {
  const groups = new Map();
  for (const c of calls) {
    if (c.status !== "missing" && c.status !== "wrong-method") continue;
    const g = groups.get(c.key) ?? {
      key: c.key,
      status: c.status,
      spec: c.spec,
      sites: [],
      apps: new Set(),
      shipped: true,
    };
    g.sites.push(c);
    g.apps.add(c.app);
    g.shipped &&= c.flags.includes("notShipped") || c.flags.includes("proposal");
    groups.set(c.key, g);
  }
  return [...groups.values()].sort((a, b) => a.key.localeCompare(b.key));
}

function analyse() {
  const spec = loadSpec();
  const calls = [...scanApp("mobile", spec), ...scanApp("console", spec)];
  const gaps = groupGaps(calls);
  const { entries, extraFixes } = loadGaps();
  const classified = new Map();
  for (const g of gaps) {
    const e = entries[g.key];
    g.entry = e ?? null;
    g.class = e && CLASSES.includes(e.class) ? e.class : "unclassified";
    classified.set(g.key, g);
  }
  const foundKeys = new Set(calls.filter((c) => c.status === "found").map((c) => c.key));
  const seenKeys = new Set(calls.map((c) => c.key).filter(Boolean));
  const stale = [];
  const invalid = [];
  for (const [key, e] of Object.entries(entries)) {
    if (key.startsWith("_")) continue;
    if (!CLASSES.includes(e.class))
      invalid.push(`${key}: class "${e.class}" is not one of ${CLASSES.join(", ")}`);
    if (foundKeys.has(key) && !classified.has(key))
      stale.push(`${key}: classified as ${e.class}, but the backend has it now`);
    else if (!seenKeys.has(key))
      stale.push(`${key}: classified as ${e.class}, but no frontend calls it any more`);
  }
  return { spec, calls, gaps, stale, invalid, extraFixes };
}

const APPS = ["mobile", "console"];
const count = (calls, app, status) =>
  calls.filter((c) => c.app === app && (status ? c.status === status : true)).length;

function renderReport({ spec, calls, gaps, stale, invalid, extraFixes }) {
  const L = [];
  const rel = (c) => c.file.replace(/^frontend\//, "");
  L.push("# Frontend ↔ backend contract gaps", "");
  L.push(
    "Generated by `just contract-gaps` (`scripts/contract-gaps.mjs`); do not edit. It compares the API calls found in the",
    `two frontends with \`backend/api/openapi.json\` (${spec.entries.length} paths, ${spec.operations} operations).`,
    "The classes come from `docs/integration/gaps.json`, which is edited by hand.",
    "",
  );
  L.push(
    "## Counts",
    "",
    "| App | Call sites | Found | Wrong method | Missing | Unresolved | Wrapped (not shipped / @proposal) |",
    "|---|--:|--:|--:|--:|--:|--:|",
  );
  for (const app of APPS) {
    const wrapped = calls.filter((c) => c.app === app && c.flags.length).length;
    L.push(
      `| ${app} | ${count(calls, app)} | ${count(calls, app, "found")} | ${count(calls, app, "wrong-method")} | ${count(calls, app, "missing")} | ${count(calls, app, "unresolved")} | ${wrapped} |`,
    );
  }
  const resolved = (app) =>
    new Set(calls.filter((c) => c.app === app && c.key).map((c) => c.key)).size;
  L.push(
    "",
    `Distinct endpoints called: mobile ${resolved("mobile")}, console ${resolved("console")}. A call site is one \`request(\`/\`apiGet(\` … in the source.`,
    "",
  );

  const byClass = (cls) => gaps.filter((g) => g.class === cls);
  L.push("## Gaps by class", "", "| Class | Endpoints | Mobile | Console |", "|---|--:|--:|--:|");
  for (const cls of ["unclassified", ...CLASSES]) {
    const list = byClass(cls);
    const sites = (app) =>
      list.reduce((n, g) => n + g.sites.filter((s) => s.app === app).length, 0);
    L.push(
      `| ${cls} | ${list.length} | ${sites("mobile")} call sites | ${sites("console")} call sites |`,
    );
  }
  L.push(
    "",
    `${gaps.length} gap endpoints in all (a call that is missing, or exists only with another method).`,
    "",
  );
  L.push(
    "Classes: `pending-pr` an open or stacked PR builds it; `fix-frontend` the frontend should change (a wrong method, id or",
    "path, or a call the spec rules out); `fix-backend` the backend lacks it and no spec says when (a frontend proposal or an",
    "unspecified view): spec it, build it, or tell the frontend to drop it; `later` a spec exists and a milestone builds it;",
    "`drop` mocked by decision, no real endpoint.",
    "",
  );

  const owners = (g) => g.entry?.owner ?? "";
  const row = (g) => {
    const files = g.sites.map((s) => `${rel(s)}:${s.line}`);
    const shown =
      files
        .slice(0, 2)
        .map((f) => `\`${f}\``)
        .join(", ") + (files.length > 2 ? ` +${files.length - 2}` : "");
    const e = g.entry ?? {};
    const when = [e.pr ? (e.pr.startsWith("#") ? `PR ${e.pr}` : e.pr) : "", e.milestone ?? ""]
      .filter(Boolean)
      .join(", ");
    const what = [
      e.fix ?? "",
      e.note ?? "",
      g.status === "wrong-method" ? `The spec has ${g.spec}.` : g.spec,
    ]
      .filter(Boolean)
      .join(" ");
    const flags = g.sites.some((s) => s.flags.length)
      ? g.shipped
        ? " (guarded)"
        : " (partly guarded)"
      : "";
    return `| \`${g.key}\`${flags} | ${[...g.apps].join(", ")} | ${shown} | ${what.replace(/\|/g, "\\|")} | ${owners(g)} | ${when} |`;
  };
  for (const cls of ["unclassified", ...CLASSES]) {
    const list = byClass(cls);
    if (!list.length) continue;
    L.push(
      `### ${cls} (${list.length})`,
      "",
      "| Call | App | Calling file | Fix / note | Owner | PR / milestone |",
      "|---|---|---|---|---|---|",
    );
    for (const g of list) L.push(row(g));
    L.push("");
  }
  L.push(
    '"(guarded)" means every call site is wrapped in the console\'s `orNotShipped(` or marked `@proposal` in the mobile source, so the screen already copes with a 404.',
    "",
  );

  if (extraFixes.length) {
    L.push(
      "## Frontend fixes that are not a missing call",
      "",
      "Mismatches the scan cannot see (mock data, roles, comments). Hand-listed in `gaps.json` under `extra_fixes`.",
      "",
      "| Id | App | Where | Fix | Owner |",
      "|---|---|---|---|---|",
      ...extraFixes.map(
        (x) =>
          `| ${x.id} | ${x.app} | ${x.where.replace(/\|/g, "\\|")} | ${x.fix.replace(/\|/g, "\\|")} | ${x.owner} |`,
      ),
      "",
    );
  }

  const unresolved = calls.filter((c) => c.status === "unresolved");
  L.push(
    `## Unresolved calls (${unresolved.length})`,
    "",
    "The static scan could not tell the method or path. They are not counted as found or missing.",
    "",
  );
  if (unresolved.length) {
    L.push("| File | Call | Why |", "|---|---|---|");
    for (const c of unresolved)
      L.push(`| \`${rel(c)}:${c.line}\` | \`${c.fn}(\` | ${c.unresolved.replace(/\|/g, "\\|")} |`);
    L.push("");
  }
  if (stale.length || invalid.length) {
    L.push(
      "## Problems in gaps.json",
      "",
      ...stale.map((s) => `- stale: ${s}`),
      ...invalid.map((s) => `- invalid: ${s}`),
      "",
    );
  }
  return `${L.join("\n")}\n`;
}

// ---------------------------------------------------------------------------------------------

function main(args) {
  const result = analyse();

  if (args.includes("--json")) {
    process.stdout.write(
      `${JSON.stringify({ calls: result.calls, gaps: result.gaps.map((g) => ({ ...g, apps: [...g.apps] })) }, null, 2)}\n`,
    );
    return 0;
  }

  const summary = APPS.map(
    (app) =>
      `${app}: ${count(result.calls, app)} call sites, ${count(result.calls, app, "found")} found, ` +
      `${count(result.calls, app, "wrong-method")} wrong method, ${count(result.calls, app, "missing")} missing, ` +
      `${count(result.calls, app, "unresolved")} unresolved`,
  );
  const where = (g) =>
    g.sites
      .slice(0, 2)
      .map((x) => `${x.file.replace(/^frontend\//, "")}:${x.line}`)
      .join(", ");

  if (args.includes("--check")) {
    const unclassified = result.gaps.filter((g) => g.class === "unclassified");
    console.log(summary.join("\n"));
    for (const g of unclassified) console.log(`unclassified: ${g.key}  (${where(g)})`);
    for (const x of result.stale) console.log(`stale: ${x}`);
    for (const x of result.invalid) console.log(`invalid: ${x}`);
    const bad = unclassified.length + result.stale.length + result.invalid.length;
    console.log(
      bad
        ? `contract-gaps: ${bad} problem(s); fix docs/integration/gaps.json, then run \`just contract-gaps\``
        : "contract-gaps: every gap is classified, nothing is stale",
    );
    return bad ? 1 : 0;
  }

  writeFileSync(REPORT, renderReport(result));
  console.log(summary.join("\n"));
  console.log(
    `${result.gaps.length} gap endpoints (${result.gaps.filter((g) => g.class === "unclassified").length} unclassified); wrote ${relative(ROOT, REPORT)}`,
  );
  return 0;
}

process.exitCode = main(process.argv.slice(2));
