#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const crypto = require("crypto");
const { fileURLToPath, pathToFileURL } = require("url");
const acorn = require("./vendor/acorn/acorn.js");

function parseArgs(argv) {
  const result = { timeout: 15000, probeWaitMs: 180, maxProbes: 60, allowRemote: false, runtimeProbe: true };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--allow-remote") { result.allowRemote = true; continue; }
    if (key === "--skip-runtime-probe") { result.runtimeProbe = false; continue; }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (value == null) throw new Error(`Missing value for ${key}`);
    result[key.slice(2).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
    index += 1;
  }
  if (Boolean(result.html) === Boolean(result.url)) throw new Error("Provide exactly one of --html or --url");
  if (!result.out) throw new Error("--out is required");
  result.timeout = Number(result.timeout);
  result.probeWaitMs = Number(result.probeWaitMs);
  result.maxProbes = Number(result.maxProbes);
  return result;
}

function contentType(filePath) {
  return ({
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
  })[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

function createStaticServer(entryPath) {
  const root = path.dirname(entryPath);
  const server = http.createServer((request, response) => {
    const requestURL = new URL(request.url, "http://127.0.0.1");
    const relative = decodeURIComponent(requestURL.pathname).replace(/^\/+/, "") || path.basename(entryPath);
    let candidate = path.resolve(root, relative);
    if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return response.writeHead(403).end("Forbidden");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) candidate = path.join(candidate, "index.html");
    if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) return response.writeHead(404).end("Not found");
    response.writeHead(200, { "Content-Type": contentType(candidate), "Cache-Control": "no-store" });
    fs.createReadStream(candidate).pipe(response);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve({ server, root, url: `http://127.0.0.1:${server.address().port}/${encodeURIComponent(path.basename(entryPath))}` }));
  });
}

function slug(value) {
  return String(value || "item").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "item";
}

function literal(node) {
  if (!node) return null;
  if (node.type === "Literal") return node.value;
  if (node.type === "TemplateLiteral" && node.expressions.length === 0) return node.quasis[0]?.value?.cooked ?? null;
  return null;
}

function memberName(node) {
  if (!node || node.type !== "MemberExpression") return null;
  return node.computed ? literal(node.property) : node.property?.name;
}

function calleeName(node) {
  if (!node) return null;
  if (node.type === "Identifier") return node.name;
  if (node.type === "MemberExpression") return memberName(node);
  return null;
}

function walk(node, visit, ancestors = []) {
  if (!node || typeof node !== "object") return;
  if (typeof node.type === "string") visit(node, ancestors);
  const next = typeof node.type === "string" ? [...ancestors, node] : ancestors;
  for (const [key, value] of Object.entries(node)) {
    if (["loc", "start", "end", "range"].includes(key)) continue;
    if (Array.isArray(value)) for (const item of value) walk(item, visit, next);
    else if (value && typeof value === "object") walk(value, visit, next);
  }
}

function walkControlled(node, visit, ancestors = []) {
  if (!node || typeof node !== "object") return;
  const isNode = typeof node.type === "string";
  if (isNode && visit(node, ancestors) === false) return;
  const next = isNode ? [...ancestors, node] : ancestors;
  for (const [key, value] of Object.entries(node)) {
    if (["loc", "start", "end", "range"].includes(key)) continue;
    if (Array.isArray(value)) for (const item of value) walkControlled(item, visit, next);
    else if (value && typeof value === "object") walkControlled(value, visit, next);
  }
}

function sourceLocation(node, script) {
  return {
    script: script.label,
    line: node.loc?.start?.line || null,
    column: node.loc?.start?.column ?? null,
    snippet: script.code.slice(node.start, Math.min(node.end, node.start + 280)).replace(/\s+/g, " ").trim(),
  };
}

function selectorFromExpression(node, bindings) {
  if (!node) return null;
  if (node.type === "Identifier") {
    if (["document", "window"].includes(node.name)) return `::${node.name}`;
    return bindings.get(node.name) || null;
  }
  if (node.type === "CallExpression" && node.callee?.type === "MemberExpression") {
    const method = memberName(node.callee);
    const value = literal(node.arguments?.[0]);
    if (method === "getElementById" && typeof value === "string") return `#${value}`;
    if (["querySelector", "querySelectorAll", "closest"].includes(method) && typeof value === "string") return value;
  }
  if (node.type === "MemberExpression") return selectorFromExpression(node.object, bindings);
  return null;
}

function parseScripts(records) {
  const scripts = [];
  const warnings = [];
  for (const record of records) {
    if (!record.code || record.code.length > 2_000_000) {
      if (record.code?.length > 2_000_000) warnings.push(`Skipped script larger than 2 MB: ${record.label}`);
      continue;
    }
    let ast = null;
    let parseError = null;
    for (const sourceType of [record.module ? "module" : "script", "module"]) {
      try {
        ast = acorn.parse(record.code, { ecmaVersion: "latest", sourceType, locations: true, allowHashBang: true });
        break;
      } catch (error) {
        parseError = error;
      }
    }
    if (ast) scripts.push({ ...record, ast });
    else warnings.push(`AST parse failed for ${record.label}: ${parseError?.message || "unknown error"}`);
  }
  return { scripts, warnings };
}

function buildStaticIndex(scripts, knownScreenHints) {
  const bindings = new Map();
  const functions = new Map();
  const parameterBindings = new Map();

  for (const script of scripts) {
    walk(script.ast, (node) => {
      if (node.type === "FunctionDeclaration" && node.id?.name) functions.set(node.id.name, { node, script });
      if (node.type === "VariableDeclarator" && node.id?.type === "Identifier") {
        const selector = selectorFromExpression(node.init, bindings);
        if (selector) bindings.set(node.id.name, selector);
        if (["ArrowFunctionExpression", "FunctionExpression"].includes(node.init?.type)) functions.set(node.id.name, { node: node.init, script });
      }
      if (node.type === "CallExpression" && memberName(node.callee) === "forEach") {
        const collection = selectorFromExpression(node.callee.object, bindings);
        const callback = node.arguments?.[0];
        const parameter = callback?.params?.[0];
        if (collection && parameter?.type === "Identifier") parameterBindings.set(parameter.name, collection);
      }
    });
  }
  for (const [name, selector] of parameterBindings) bindings.set(name, selector);

  const listeners = [];
  for (const script of scripts) {
    walk(script.ast, (node) => {
      if (node.type === "CallExpression" && memberName(node.callee) === "addEventListener") {
        const event = literal(node.arguments?.[0]);
        const handlerNode = node.arguments?.[1];
        const sourceSelector = selectorFromExpression(node.callee.object, bindings);
        const handler = handlerNode?.type === "Identifier" ? functions.get(handlerNode.name) : { node: handlerNode, script };
        if (!event || !handler?.node) return;
        listeners.push({ event: String(event), sourceSelector, handler: { ...handler, sourceSelector }, registration: sourceLocation(node, script), explicitProperty: false });
      }
      if (node.type === "AssignmentExpression" && node.left?.type === "MemberExpression" && /^on/.test(memberName(node.left) || "")) {
        const event = memberName(node.left).slice(2);
        const sourceSelector = selectorFromExpression(node.left.object, bindings);
        const handlerNode = node.right;
        const handler = handlerNode?.type === "Identifier" ? functions.get(handlerNode.name) : { node: handlerNode, script };
        if (handler?.node) listeners.push({ event, sourceSelector, handler: { ...handler, sourceSelector }, registration: sourceLocation(node, script), explicitProperty: true });
      }
    });
  }

  const ownerHints = new Map();
  for (const { node: functionNode } of functions.values()) {
    walk(functionNode.body, (node) => {
      if (node.type !== "IfStatement") return;
      const screenHint = [...knownScreenHints].find((hint) => {
        let found = false;
        walk(node.test, (candidate) => { if (literal(candidate) === hint) found = true; });
        return found;
      });
      if (!screenHint) return;
      walk(node.consequent, (candidate) => {
        if (candidate.type === "CallExpression" && candidate.callee?.type === "Identifier" && functions.has(candidate.callee.name)) {
          ownerHints.set(candidate.callee.name, screenHint);
        }
      });
    });
  }
  return { bindings, functions, listeners, ownerHints };
}

function collectEffects(handler, index, knownScreenHints, inheritedSchedule = null, stack = [], callDepth = 0) {
  const effects = [];
  if (!handler?.node || stack.includes(handler.node)) return effects;
  const nextStack = [...stack, handler.node];
  const localBindings = new Map(index.bindings);
  for (const parameter of handler.node.params || []) {
    if (parameter.type === "Identifier" && handler.sourceSelector) localBindings.set(parameter.name, handler.sourceSelector);
  }
  const localIndex = { ...index, bindings: localBindings };
  const traversalRoot = handler.node.body || handler.node;
  walkControlled(traversalRoot, (node) => {
    if (["FunctionExpression", "ArrowFunctionExpression", "FunctionDeclaration"].includes(node.type) && node !== handler.node && node !== traversalRoot) return false;

    if (node.type === "CallExpression") {
      const name = calleeName(node.callee);
      const schedule = ["setTimeout", "setInterval"].includes(name)
        ? { type: name === "setTimeout" ? "delay" : "interval", ms: Number(literal(node.arguments?.[1])) || null }
        : inheritedSchedule;
      if (["setTimeout", "setInterval"].includes(name)) {
        const callback = node.arguments?.[0];
        if (callback) effects.push(...collectEffects({ node: callback, script: handler.script, sourceSelector: handler.sourceSelector }, localIndex, knownScreenHints, schedule, nextStack, callDepth));
        return false;
      }
      if (["forEach", "map", "filter", "some", "every", "requestAnimationFrame"].includes(name)) {
        const callback = node.arguments?.[0];
        if (callback && ["FunctionExpression", "ArrowFunctionExpression"].includes(callback.type)) {
          effects.push(...collectEffects({ node: callback, script: handler.script, sourceSelector: handler.sourceSelector }, localIndex, knownScreenHints, inheritedSchedule, nextStack, callDepth));
        }
        return false;
      }

      const firstValue = literal(node.arguments?.[0]);
      if (typeof firstValue === "string" && knownScreenHints.has(firstValue) && name && !["getElementById", "querySelector", "querySelectorAll"].includes(name)) {
        effects.push({ type: "screen-transition", targetHint: firstValue, functionName: name, schedule, analysisDepth: callDepth, evidence: sourceLocation(node, handler.script) });
        return;
      }
      if (name === "open" && node.callee?.object?.name === "window") {
        effects.push({ type: "open-url", value: firstValue, schedule, evidence: sourceLocation(node, handler.script) });
        return;
      }
      if (["assign", "replace"].includes(name) && node.callee?.object?.name === "location") {
        effects.push({ type: "location", mode: name, value: firstValue, schedule, evidence: sourceLocation(node, handler.script) });
        return;
      }
      if (["back", "forward", "go", "pushState", "replaceState"].includes(name) && node.callee?.object?.name === "history") {
        effects.push({ type: "history", mode: name, value: firstValue, schedule, evidence: sourceLocation(node, handler.script) });
        return;
      }
      if (["add", "remove", "toggle"].includes(name) && memberName(node.callee?.object) === "classList") {
        const target = selectorFromExpression(node.callee.object.object, localBindings);
        const classes = node.arguments.map(literal).filter((value) => typeof value === "string");
        effects.push({ type: "class-mutation", mode: name, targetSelector: target, classes, schedule, evidence: sourceLocation(node, handler.script) });
        return;
      }
      if (name === "setAttribute") {
        const target = selectorFromExpression(node.callee.object, localBindings);
        effects.push({ type: "attribute-mutation", targetSelector: target, attribute: firstValue, value: literal(node.arguments?.[1]), schedule, evidence: sourceLocation(node, handler.script) });
        return;
      }
      if (node.callee?.type === "Identifier" && index.functions.has(node.callee.name)) {
        effects.push(...collectEffects(index.functions.get(node.callee.name), localIndex, knownScreenHints, schedule, nextStack, callDepth + 1));
        return false;
      }
    }

    if (node.type === "VariableDeclarator" && node.id?.type === "Identifier") {
      const selector = selectorFromExpression(node.init, localBindings);
      if (selector) localBindings.set(node.id.name, selector);
    }

    if (node.type === "AssignmentExpression" && node.left?.type === "MemberExpression") {
      const property = memberName(node.left);
      let target = selectorFromExpression(node.left.object, localBindings);
      if (node.left.object?.type === "MemberExpression" && memberName(node.left.object) === "style") target = selectorFromExpression(node.left.object.object, localBindings);
      if (["textContent", "innerHTML", "value", "display", "opacity", "visibility", "width", "height", "color", "transform"].includes(property)) {
        effects.push({ type: property === "textContent" || property === "innerHTML" || property === "value" ? "content-mutation" : "style-mutation", targetSelector: target, property, value: literal(node.right), schedule: inheritedSchedule, evidence: sourceLocation(node, handler.script) });
      }
    }
  });
  const seen = new Set();
  return effects.filter((effect) => {
    const key = JSON.stringify([effect.type, effect.mode, effect.targetHint, effect.targetSelector, effect.classes, effect.property, effect.schedule]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function inferStateKind(metadata, effect) {
  const blob = `${metadata?.id || ""} ${metadata?.className || ""} ${effect.targetSelector || ""}`.toLowerCase();
  if (/sheet|drawer|bottom-sheet/.test(blob)) return "sheet";
  if (/fullscreen|full-screen|fs-overlay/.test(blob)) return "full-screen-overlay";
  if (/popover|menu|emoji|tooltip/.test(blob)) return "popover-overlay";
  if (/overlay|mask|modal|dialog/.test(blob)) return "overlay";
  if (/expand|collapse|accordion|dims-wrap|open/.test(blob)) return "expansion";
  if (/active|selected|platform|tab|chip|check|chk/.test(blob)) return "selection";
  if (/progress|prog-fill|ring/.test(blob)) return "progress-animation";
  if (effect.type === "content-mutation") return "transient-feedback";
  return "local-state";
}

function nativeCandidates(sourceScreenId, targetScreenId, entryScreenId, automatic) {
  if (targetScreenId === entryScreenId && sourceScreenId && sourceScreenId !== entryScreenId) return ["pop-to-root", "replace-root", "replace-flow-state"];
  if (automatic) return ["replace-flow-state", "push", "replace"];
  return ["replace-flow-state", "push"];
}

function isVisibleSnapshotDifferent(before, after) {
  if (!before || !after) return false;
  return JSON.stringify(before.screens) !== JSON.stringify(after.screens)
    || JSON.stringify(before.targets) !== JSON.stringify(after.targets)
    || before.url !== after.url;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { chromium } = require("playwright");
  let server = null;
  let entryURL = args.url;
  let localRoot = null;
  const warnings = [];
  if (args.html) {
    const entry = path.resolve(args.html);
    if (!fs.existsSync(entry)) throw new Error(`HTML entry does not exist: ${entry}`);
    localRoot = path.dirname(entry);
    try {
      const hosted = await createStaticServer(entry);
      server = hosted.server;
      entryURL = hosted.url;
    } catch (error) {
      entryURL = pathToFileURL(entry).href;
      warnings.push(`Local HTTP server unavailable; fell back to file URL: ${error.code || error.message}`);
    }
  }
  const routeGraph = args.routeGraph ? JSON.parse(fs.readFileSync(path.resolve(args.routeGraph), "utf8")) : null;
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", acceptDownloads: false });
  const allowedOrigin = new URL(entryURL).origin;
  if (!args.allowRemote) await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    let allowed = ["data:", "blob:"].includes(url.protocol) || url.origin === allowedOrigin;
    if (url.protocol === "file:" && localRoot) {
      const requestedPath = path.resolve(fileURLToPath(url));
      allowed = requestedPath === localRoot || requestedPath.startsWith(`${localRoot}${path.sep}`);
    }
    if (allowed) await route.continue();
    else await route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  try {
    await page.goto(entryURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
    await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
    const scriptRecords = await page.evaluate(async (origin) => {
      const records = [];
      for (const [index, script] of Array.from(document.scripts).entries()) {
        const module = script.type === "module";
        if (!script.src) {
          records.push({ label: `inline-script-${index + 1}`, code: script.textContent || "", module });
          continue;
        }
        try {
          const url = new URL(script.src, location.href);
          if (url.origin !== origin) {
            records.push({ label: url.href, code: "", module, skipped: "cross-origin" });
            continue;
          }
          records.push({ label: url.href, code: await (await fetch(url.href)).text(), module });
        } catch (error) {
          records.push({ label: script.src, code: "", module, skipped: error.message });
        }
      }
      return records;
    }, allowedOrigin);
    for (const record of scriptRecords.filter((item) => item.skipped)) warnings.push(`Skipped ${record.label}: ${record.skipped}`);
    const parsed = parseScripts(scriptRecords);
    warnings.push(...parsed.warnings);

    let screens = (routeGraph?.screens || []).filter((screen) => screen.includeInNativeConversion !== false).map((screen) => ({
      id: screen.id,
      virtualStateId: screen.virtualStateId || screen.id,
      rootSelector: screen.rootSelector || null,
      activation: screen.activation || null,
    }));
    if (!screens.length) screens = await page.evaluate(() => Array.from(document.querySelectorAll(".page[id], [role=tabpanel][id], [data-screen-id]")).map((element) => ({
      id: element.id || element.getAttribute("data-screen-id"),
      virtualStateId: element.id || element.getAttribute("data-screen-id"),
      rootSelector: element.id ? `#${CSS.escape(element.id)}` : `[data-screen-id="${CSS.escape(element.getAttribute("data-screen-id"))}"]`,
      activation: null,
    })));
    const screenByHint = new Map();
    for (const screen of screens) {
      screenByHint.set(screen.id, screen.id);
      screenByHint.set(screen.virtualStateId, screen.id);
      if (screen.rootSelector) screenByHint.set(screen.rootSelector, screen.id);
    }
    const knownScreenHints = new Set([...screenByHint.keys()].filter((value) => value && !String(value).startsWith("#")));
    const entryScreenId = screens[0]?.id || null;
    const staticIndex = buildStaticIndex(parsed.scripts, knownScreenHints);

    const selectors = Array.from(new Set([
      ...staticIndex.listeners.map((item) => item.sourceSelector),
      ...staticIndex.listeners.flatMap((item) => collectEffects(item.handler, staticIndex, knownScreenHints).map((effect) => effect.targetSelector)),
    ].filter((selector) => selector && !selector.startsWith("::"))));
    const metadataBySelector = new Map(Object.entries(await page.evaluate((selectors) => {
      const result = {};
      for (const selector of selectors) {
        try {
          const element = document.querySelector(selector);
          if (!element) continue;
          const screen = element.closest(".page[id], [role=tabpanel][id], [data-screen-id]");
          result[selector] = {
            id: element.id || null,
            tag: element.tagName.toLowerCase(),
            type: element.getAttribute("type"),
            href: element.getAttribute("href"),
            className: typeof element.className === "string" ? element.className : "",
            text: (element.textContent || "").trim().slice(0, 180),
            screenHint: screen ? screen.id || screen.getAttribute("data-screen-id") : null,
            ancestorSelectors: Array.from(function* () {
              let current = element;
              while (current && current !== document.documentElement) {
                if (current.id) yield `#${CSS.escape(current.id)}`;
                current = current.parentElement;
              }
            }()).slice(0, 12),
          };
        } catch (_) {}
      }
      return result;
    }, selectors)));

    const states = [];
    const interactions = [];
    const transitions = [];
    const unresolved = [];
    const stateByKey = new Map();
    const listenerRecords = staticIndex.listeners.map((listener) => ({
      listener,
      sourceMetadata: metadataBySelector.get(listener.sourceSelector),
      effects: collectEffects(listener.handler, staticIndex, knownScreenHints),
      ambientScope: listener.sourceSelector?.startsWith("::") ? listener.sourceSelector.slice(2) : null,
    }));
    const targetOwnerHints = new Map();
    for (const record of listenerRecords) {
      const directOwner = screenByHint.get(record.sourceMetadata?.screenHint) || null;
      if (!directOwner) continue;
      for (const effect of record.effects) {
        if (effect.targetSelector && !screenByHint.get(metadataBySelector.get(effect.targetSelector)?.screenHint)) {
          targetOwnerHints.set(effect.targetSelector, directOwner);
        }
      }
    }

    listenerRecords.forEach(({ listener, sourceMetadata, effects, ambientScope }) => {
      let sourceScreenId = screenByHint.get(sourceMetadata?.screenHint) || null;
      if (!sourceScreenId) {
        const ancestorOwners = (sourceMetadata?.ancestorSelectors || []).map((selector) => targetOwnerHints.get(selector)).filter(Boolean);
        if (ancestorOwners.length && new Set(ancestorOwners).size === 1) sourceScreenId = ancestorOwners[0];
      }
      if (!sourceScreenId && ambientScope) {
        const effectOwners = effects.map((effect) => screenByHint.get(metadataBySelector.get(effect.targetSelector)?.screenHint) || targetOwnerHints.get(effect.targetSelector)).filter(Boolean);
        sourceScreenId = effectOwners.length && new Set(effectOwners).size === 1 ? effectOwners[0] : null;
      }
      if (!sourceScreenId) {
        const navigationTargets = effects.filter((effect) => effect.type === "screen-transition" && effect.analysisDepth === 0).map((effect) => screenByHint.get(effect.targetHint)).filter(Boolean);
        if (navigationTargets.length === 1 && navigationTargets[0] !== entryScreenId) sourceScreenId = entryScreenId;
      }
      if (!sourceScreenId && !ambientScope) return;
      const interactionId = `interaction-${interactions.length + 1}`;
      const trigger = listener.event === "click" ? "tap" : listener.event === "change" || listener.event === "input" ? "change" : listener.event;
      const interaction = {
        id: interactionId,
        sourceSelector: ambientScope ? null : listener.sourceSelector,
        sourceScope: ambientScope,
        sourceScreenId,
        sourceText: sourceMetadata?.text || null,
        trigger,
        classification: effects.some((effect) => effect.type === "screen-transition") ? "navigation"
          : effects.some((effect) => effect.type === "class-mutation") ? "state-change"
          : effects.some((effect) => effect.type === "content-mutation") ? "content-change"
          : "event-handler",
        safety: ambientScope ? { runtimeProbe: "skipped", reason: "ambient-event-source" } : { runtimeProbe: "eligible", reason: null },
        astEvidence: { registration: listener.registration, effects },
        runtimeEvidence: null,
        confidence: listener.sourceSelector ? 0.82 : 0.58,
      };
      if (!listener.sourceSelector && !ambientScope) {
        interaction.safety = { runtimeProbe: "skipped", reason: "source-selector-unresolved" };
        unresolved.push({ id: `unresolved-${unresolved.length + 1}`, kind: "source-selector", interactionId, question: "Which DOM control owns this event handler?", candidates: [], recommended: null, evidence: listener.registration });
      }
      interactions.push(interaction);

      for (const effect of effects) {
        if (effect.type === "screen-transition") {
          const targetScreenId = screenByHint.get(effect.targetHint) || null;
          const automatic = Boolean(effect.schedule);
          const candidates = nativeCandidates(sourceScreenId, targetScreenId, entryScreenId, automatic);
          const transition = {
            id: `transition-${transitions.length + 1}`,
            interactionId,
            sourceScreenId,
            targetScreenId,
            trigger: automatic ? "automatic" : trigger,
            kind: automatic ? "automatic-navigation" : "navigation",
            webAction: effect.functionName,
            recommendedNativeAction: candidates[0],
            nativeActionCandidates: candidates,
            schedule: effect.schedule,
            confidence: targetScreenId ? 0.86 : 0.55,
            evidence: effect.evidence,
            requiresOverride: candidates.length > 1,
          };
          transitions.push(transition);
          if (!targetScreenId || transition.requiresOverride) unresolved.push({
            id: `unresolved-${unresolved.length + 1}`,
            kind: targetScreenId ? "native-navigation-ownership" : "target-screen",
            transitionId: transition.id,
            question: targetScreenId ? `Choose the native ownership for ${sourceScreenId || "unknown"} → ${targetScreenId}.` : `Resolve target screen ${effect.targetHint}.`,
            candidates,
            recommended: candidates[0] || null,
            evidence: effect.evidence,
          });
          continue;
        }
        if (["class-mutation", "style-mutation", "content-mutation", "attribute-mutation"].includes(effect.type)) {
          const targetMetadata = metadataBySelector.get(effect.targetSelector);
          const ownerScreenId = screenByHint.get(targetMetadata?.screenHint) || targetOwnerHints.get(effect.targetSelector) || sourceScreenId;
          const kind = inferStateKind(targetMetadata, effect);
          const stateKey = `${ownerScreenId}|${effect.targetSelector}|${kind}`;
          let stateId = stateByKey.get(stateKey);
          if (!stateId) {
            stateId = `state-${states.length + 1}-${slug(targetMetadata?.id || kind)}`;
            stateByKey.set(stateKey, stateId);
            states.push({ id: stateId, ownerScreenId, kind, targetSelector: effect.targetSelector, targetElementId: targetMetadata?.id || null, classes: effect.classes || [], confidence: effect.targetSelector ? 0.88 : 0.55 });
          }
          const presentation = ["sheet", "full-screen-overlay", "popover-overlay", "overlay"].includes(kind);
          transitions.push({
            id: `transition-${transitions.length + 1}`,
            interactionId,
            sourceScreenId,
            targetStateId: stateId,
            trigger,
            kind: presentation ? "presentation" : "local-state",
            webAction: effect.mode || effect.property || effect.type,
            recommendedNativeAction: kind === "sheet" ? (effect.mode === "remove" ? "dismiss" : "sheet")
              : kind === "full-screen-overlay" ? (effect.mode === "remove" ? "dismiss" : "full-screen-cover")
              : kind === "popover-overlay" ? "popover-or-overlay"
              : kind === "expansion" ? "toggle-expanded"
              : kind === "selection" ? "update-selection"
              : "update-local-state",
            schedule: effect.schedule,
            confidence: effect.targetSelector ? 0.84 : 0.5,
            evidence: effect.evidence,
            requiresOverride: !effect.targetSelector,
          });
        }
      }
    });

    for (const [functionName, ownerHint] of staticIndex.ownerHints) {
      const handler = staticIndex.functions.get(functionName);
      const scheduled = collectEffects(handler, staticIndex, knownScreenHints).filter((effect) => effect.type === "screen-transition" && effect.schedule);
      for (const effect of scheduled) {
        const sourceScreenId = screenByHint.get(ownerHint) || null;
        const targetScreenId = screenByHint.get(effect.targetHint) || null;
        if (transitions.some((item) => !item.interactionId && item.sourceScreenId === sourceScreenId && item.targetScreenId === targetScreenId)) continue;
        const candidates = nativeCandidates(sourceScreenId, targetScreenId, entryScreenId, true);
        const transition = {
          id: `transition-${transitions.length + 1}`,
          interactionId: null,
          sourceScreenId,
          targetScreenId,
          trigger: "automatic",
          kind: "automatic-navigation",
          webAction: functionName,
          recommendedNativeAction: candidates[0],
          nativeActionCandidates: candidates,
          schedule: effect.schedule,
          confidence: sourceScreenId && targetScreenId ? 0.82 : 0.52,
          evidence: effect.evidence,
          requiresOverride: true,
        };
        transitions.push(transition);
        unresolved.push({ id: `unresolved-${unresolved.length + 1}`, kind: "native-navigation-ownership", transitionId: transition.id, question: `Choose the native ownership for automatic ${sourceScreenId} → ${targetScreenId}.`, candidates, recommended: candidates[0], evidence: effect.evidence });
      }
    }

    async function snapshot(probePage, targetSelectors) {
      return probePage.evaluate(({ screens, targetSelectors }) => {
        const visible = (element) => {
          if (!element) return false;
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0.01 && rect.width > 0 && rect.height > 0;
        };
        const screenState = {};
        for (const screen of screens) screenState[screen.id] = visible(document.querySelector(screen.rootSelector));
        const targets = {};
        for (const selector of targetSelectors) {
          try {
            const element = document.querySelector(selector);
            targets[selector] = element ? { visible: visible(element), classes: Array.from(element.classList).sort(), text: (element.textContent || "").trim().slice(0, 120) } : null;
          } catch (_) { targets[selector] = null; }
        }
        return { url: location.href, screens: screenState, targets };
      }, { screens: screens.filter((screen) => screen.rootSelector), targetSelectors });
    }

    if (args.runtimeProbe) {
      const openerByTarget = new Map();
      for (const interaction of interactions) {
        if (!interaction.sourceSelector) continue;
        for (const effect of interaction.astEvidence.effects) {
          const kind = inferStateKind(metadataBySelector.get(effect.targetSelector), effect);
          const revealsContainer = ["sheet", "full-screen-overlay", "popover-overlay", "overlay", "expansion"].includes(kind);
          if (revealsContainer && effect.type === "class-mutation" && ["add", "toggle"].includes(effect.mode) && effect.targetSelector) {
            openerByTarget.set(effect.targetSelector, interaction.sourceSelector);
          }
        }
      }
      let probed = 0;
      for (const interaction of interactions) {
        if (probed >= args.maxProbes || interaction.safety.runtimeProbe !== "eligible" || interaction.trigger !== "tap") continue;
        const meta = metadataBySelector.get(interaction.sourceSelector);
        if (!meta) { interaction.safety = { runtimeProbe: "skipped", reason: "element-metadata-unavailable" }; continue; }
        const unsafe = meta.type === "file" || meta.type === "submit" || Boolean(meta.href && /^https?:/i.test(meta.href)) || /删除|支付|购买|提交|发送|注销|delete|pay|purchase|submit|send/i.test(meta.text || "");
        if (unsafe) { interaction.safety = { runtimeProbe: "skipped", reason: "potential-side-effect" }; continue; }
        const probePage = await context.newPage();
        let dialogSeen = false;
        probePage.on("dialog", async (dialog) => { dialogSeen = true; await dialog.dismiss(); });
        try {
          await probePage.goto(entryURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
          await probePage.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
          const sourceScreen = screens.find((screen) => screen.id === interaction.sourceScreenId);
          const targetHint = sourceScreen?.virtualStateId || sourceScreen?.id;
          const escapedHint = String(targetHint || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
          const activationSelectors = targetHint ? [
            `[data-page="${escapedHint}"]`, `[data-screen="${escapedHint}"]`, `[data-route="${escapedHint}"]`, `[aria-controls="${escapedHint}"]`,
            ...(sourceScreen?.activation?.selectors || []),
          ] : (sourceScreen?.activation?.selectors || []);
          for (const activationSelector of activationSelectors) {
            const activation = probePage.locator(activationSelector).first();
            if (await activation.count() && await activation.isVisible()) { await activation.click(); break; }
          }
          const prerequisiteTargets = Array.from(new Set([interaction.sourceSelector, ...(meta.ancestorSelectors || [])]));
          for (const target of prerequisiteTargets) {
            const openerSelector = openerByTarget.get(target);
            if (!openerSelector || openerSelector === interaction.sourceSelector) continue;
            const opener = probePage.locator(openerSelector).first();
            if (await opener.count() && await opener.isVisible()) {
              await opener.click();
              if (args.probeWaitMs) await probePage.waitForTimeout(Math.min(args.probeWaitMs, 120));
            }
          }
          const targetSelectors = Array.from(new Set(interaction.astEvidence.effects.map((effect) => effect.targetSelector).filter(Boolean)));
          const before = await snapshot(probePage, targetSelectors);
          const candidates = probePage.locator(interaction.sourceSelector);
          let locator = candidates.first();
          const candidateCount = await candidates.count();
          if (candidateCount > 1) {
            for (let index = 0; index < candidateCount; index += 1) {
              const candidate = candidates.nth(index);
              if (await candidate.isVisible() && !await candidate.evaluate((element) => element.classList.contains("active") || element.getAttribute("aria-selected") === "true")) {
                locator = candidate;
                break;
              }
            }
          }
          if (!candidateCount || !await locator.isVisible()) {
            interaction.safety = { runtimeProbe: "skipped", reason: "source-not-visible-after-activation" };
            continue;
          }
          const backdropLike = /overlay|mask|backdrop/.test(`${meta.id || ""} ${meta.className || ""}`.toLowerCase());
          const probeMethod = backdropLike ? "synthetic-backdrop-click" : "playwright-click";
          if (backdropLike) await locator.evaluate((element) => element.click());
          else await locator.click({ timeout: Math.min(args.timeout, 5000) });
          if (args.probeWaitMs) await probePage.waitForTimeout(args.probeWaitMs);
          const after = await snapshot(probePage, targetSelectors);
          interaction.runtimeEvidence = { status: "verified", changed: isVisibleSnapshotDifferent(before, after), probeMethod, before, after, dialogSeen };
          if (interaction.runtimeEvidence.changed) interaction.confidence = Math.min(1, interaction.confidence + 0.12);
          probed += 1;
        } catch (error) {
          interaction.runtimeEvidence = { status: "failed", error: error.message };
        } finally {
          await probePage.close();
        }
      }
    }

    const sourceFingerprint = crypto.createHash("sha256").update(args.html
      ? fs.readFileSync(path.resolve(args.html))
      : `${entryURL}\n${scriptRecords.map((record) => `${record.label}\n${record.code}`).join("\n")}`).digest("hex");
    const output = {
      schemaVersion: "interaction-state-graph-1.0",
      source: args.html ? { kind: "html-file", entry: path.resolve(args.html), root: localRoot, fingerprint: sourceFingerprint } : { kind: "url", entry: args.url, fingerprint: sourceFingerprint },
      routeGraph: args.routeGraph ? path.resolve(args.routeGraph) : null,
      capabilities: {
        ast: { status: parsed.scripts.length ? "available" : "not-run", parser: "acorn", version: "8.17.0", scriptsParsed: parsed.scripts.length },
        runtimeProbe: { status: args.runtimeProbe ? "available" : "not-run", policy: "fresh-page-safe-clicks-only" },
      },
      screens,
      states,
      interactions,
      transitions,
      unresolved,
      warnings: Array.from(new Set(warnings)),
      summary: {
        screens: screens.length,
        states: states.length,
        interactions: interactions.length,
        transitions: transitions.length,
        automaticTransitions: transitions.filter((item) => item.trigger === "automatic").length,
        runtimeVerified: interactions.filter((item) => item.runtimeEvidence?.status === "verified").length,
        unresolved: unresolved.length,
      },
    };
    const out = path.resolve(args.out);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, `${JSON.stringify(output, null, 2)}\n`, "utf8");
    const overridesPath = path.resolve(args.overridesOut || path.join(path.dirname(out), "html-to-ios.overrides.json"));
    const overrides = {
      schemaVersion: "html-to-ios-overrides-1.0",
      generatedFrom: out,
      sourceFingerprint,
      resolutions: [],
      unresolved: unresolved.map((item) => ({ id: item.id, kind: item.kind, transitionId: item.transitionId || null, interactionId: item.interactionId || null, question: item.question, candidates: item.candidates, recommended: item.recommended, resolution: null })),
    };
    fs.writeFileSync(overridesPath, `${JSON.stringify(overrides, null, 2)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify({ out, overrides: overridesPath, ...output.summary }, null, 2)}\n`);
  } finally {
    await page.close();
    await context.close();
    await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
