#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { fileURLToPath, pathToFileURL } = require("url");
const { classifyScreenRepresentations } = require("./artboard_state_classifier.cjs");

function parseArgs(argv) {
  const result = { timeout: 15000, maxPages: 40, allowRemote: false };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--allow-remote") { result.allowRemote = true; continue; }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (value === undefined) throw new Error(`Missing value for ${key}`);
    result[key.slice(2).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
    index += 1;
  }
  if (Boolean(result.html) === Boolean(result.url)) throw new Error("Provide exactly one of --html or --url");
  if (!result.out) throw new Error("--out is required");
  result.timeout = Number(result.timeout);
  result.maxPages = Number(result.maxPages);
  return result;
}

function contentType(filePath) {
  return ({
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
  })[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

function createStaticServer(entryPath) {
  const root = path.dirname(entryPath);
  const server = http.createServer((request, response) => {
    const requestURL = new URL(request.url, "http://127.0.0.1");
    const decoded = decodeURIComponent(requestURL.pathname).replace(/^\/+/, "");
    let candidate = path.resolve(root, decoded || path.basename(entryPath));
    if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return response.writeHead(403).end("Forbidden");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) candidate = path.join(candidate, "index.html");
    if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) return response.writeHead(404).end("Not found");
    response.writeHead(200, { "Content-Type": contentType(candidate), "Cache-Control": "no-store" });
    fs.createReadStream(candidate).pipe(response);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({ server, root, url: `http://127.0.0.1:${address.port}/${encodeURIComponent(path.basename(entryPath))}` });
    });
  });
}

function slug(value) {
  return String(value || "screen").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "screen";
}

function screenId(url, used) {
  const parsed = new URL(url);
  const basename = path.posix.basename(parsed.pathname) || "index";
  let base = slug(basename === "index.html" ? path.posix.basename(path.posix.dirname(parsed.pathname)) || "home" : basename);
  if (parsed.hash && parsed.hash !== "#") base += `-${slug(parsed.hash.slice(1))}`;
  let result = base;
  let suffix = 2;
  while (used.has(result)) result = `${base}-${suffix++}`;
  used.add(result);
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const playwright = require("playwright");
  let server = null;
  let localRoot = null;
  let entryURL = args.url;
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
  const browser = await playwright.chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 393, height: 852 }, reducedMotion: "reduce" });
  const allowedOrigin = new URL(entryURL).origin;
  if (!args.allowRemote) {
    await context.route("**/*", async (route) => {
      const requestURL = new URL(route.request().url());
      let allowed = ["data:", "blob:"].includes(requestURL.protocol) || requestURL.origin === allowedOrigin;
      if (requestURL.protocol === "file:" && localRoot) {
        const requestedPath = path.resolve(fileURLToPath(requestURL));
        allowed = requestedPath === localRoot || requestedPath.startsWith(`${localRoot}${path.sep}`);
      }
      if (allowed) await route.continue(); else await route.abort("blockedbyclient");
    });
  }
  const queue = [entryURL];
  const visited = new Set();
  const idByURL = new Map();
  const usedIds = new Set();
  const screens = [];
  const rawEdges = [];
  try {
    while (queue.length && screens.length < args.maxPages) {
      const requestedURL = queue.shift();
      const normalizedURL = new URL(requestedURL).href;
      if (visited.has(normalizedURL)) continue;
      visited.add(normalizedURL);
      const page = await context.newPage();
      try {
        const response = await page.goto(normalizedURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
        if (response && response.status() >= 400) {
          warnings.push(`Route returned HTTP ${response.status()}: ${normalizedURL}`);
          continue;
        }
        await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
        const actualURL = page.url();
        const id = screenId(actualURL, usedIds);
        idByURL.set(actualURL, id);
        idByURL.set(normalizedURL, id);
        const discovered = await page.evaluate(() => {
          const cssPath = (element) => {
            if (element.id) return `#${CSS.escape(element.id)}`;
            for (const attribute of ["data-ios-screen", "data-ios-node-id", "data-ios-target", "data-page", "data-screen", "data-route", "aria-controls", "data-ios-action"]) {
              const value = element.getAttribute(attribute);
              if (value == null) continue;
              const candidate = `${element.tagName.toLowerCase()}[${attribute}=${JSON.stringify(value)}]`;
              if (document.querySelectorAll(candidate).length === 1) return candidate;
            }
            const base = `${element.tagName.toLowerCase()}${element.classList.length ? `.${Array.from(element.classList).slice(0, 2).map(CSS.escape).join(".")}` : ""}`;
            const matches = Array.from(document.querySelectorAll(base));
            if (matches.length <= 1 || !element.parentElement) return base;
            const sameTagSiblings = Array.from(element.parentElement.children).filter((sibling) => sibling.tagName === element.tagName);
            const index = sameTagSiblings.indexOf(element);
            return `${cssPath(element.parentElement)} > ${base}:nth-of-type(${index + 1})`;
          };
          const links = Array.from(document.querySelectorAll("a[href]")).map((element) => ({
            selector: cssPath(element), href: element.href, rawHref: element.getAttribute("href"), target: element.target || null,
            text: (element.textContent || "").trim().slice(0, 160), role: element.getAttribute("role"),
          }));
          const forms = Array.from(document.querySelectorAll("form")).map((element) => ({
            selector: cssPath(element), action: element.action || location.href, method: (element.method || "get").toLowerCase(),
          }));
          const explicit = Array.from(document.querySelectorAll("[data-ios-action]")).map((element) => ({
            selector: cssPath(element), action: element.getAttribute("data-ios-action"), target: element.getAttribute("data-ios-target"),
            presentationStyle: element.getAttribute("data-ios-presentation-style"),
            sourceScreenHint: (
              element.closest("[data-ios-screen], .page[id], [role=tabpanel][id], [data-screen-id]")?.getAttribute("data-ios-screen")
              || element.closest("[data-ios-screen], .page[id], [role=tabpanel][id], [data-screen-id]")?.id
              || element.closest("[data-ios-screen], .page[id], [role=tabpanel][id], [data-screen-id]")?.getAttribute("data-screen-id")
              || null
            ),
          }));
          const visualSnapshot = (root) => {
            const rootRect = root.getBoundingClientRect();
            const allElements = [root, ...root.querySelectorAll("*")];
            const visibleElements = allElements.filter((element) => {
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              return (
                style.display !== "none"
                && style.visibility !== "hidden"
                && Number(style.opacity || 1) > 0.01
                && rect.width > 0
                && rect.height > 0
              );
            });
            const elements = visibleElements.length >= 3 ? visibleElements : allElements;
            const depth = (element) => {
              let value = 0;
              let current = element;
              while (current && current !== root) {
                value += 1;
                current = current.parentElement;
              }
              return value;
            };
            const structureTokens = elements.map((element) => {
              const classes = Array.from(element.classList)
                .filter((name) => !/^(active|current|open|opened|show|shown|selected|expanded|visible)$/i.test(name))
                .slice(0, 4)
                .sort();
              const semantic = element.getAttribute("data-ios-component")
                || element.getAttribute("role")
                || element.getAttribute("type")
                || "";
              return `${depth(element)}:${element.tagName.toLowerCase()}:${semantic}:${classes.join(".")}`;
            });
            const textTokens = elements
              .filter((element) => element.children.length === 0)
              .map((element) => (element.textContent || "").replace(/\d+/g, "#").replace(/\s+/g, " ").trim().toLowerCase())
              .filter((text) => text && text.length <= 80);
            const positionedAreaRatio = elements.reduce((maximum, element) => {
              const style = getComputedStyle(element);
              if (!["absolute", "fixed", "sticky"].includes(style.position)) return maximum;
              const rect = element.getBoundingClientRect();
              const rootArea = Math.max(rootRect.width * rootRect.height, 1);
              return Math.max(maximum, Math.min((rect.width * rect.height) / rootArea, 1));
            }, 0);
            const hints = elements.flatMap((element) => [
              element.id,
              ...Array.from(element.classList),
              element.getAttribute("data-ios-state-kind"),
              element.getAttribute("data-ios-presentation-style"),
            ]).filter(Boolean);
            return {
              nodeCount: elements.length,
              structureTokens,
              textTokens,
              positionedAreaRatio,
              hints: Array.from(new Set(hints)).slice(0, 120),
            };
          };
          const visualEnvelope = (target) => {
            const explicit = target.getAttribute("data-ios-visual-root");
            let visualRoot = explicit ? document.querySelector(explicit) : null;
            if (!visualRoot) {
              let current = target.parentElement;
              while (current && current !== document.body) {
                if (
                  current.hasAttribute("data-ios-container")
                  || current.matches(".screen, .phone-screen, .mobile, .mobile-screen, .app-screen, [role=application]")
                ) {
                  visualRoot = current;
                  break;
                }
                current = current.parentElement;
              }
            }
            visualRoot ||= target;
            const sharedRegionSelectors = [];
            for (const element of visualRoot.querySelectorAll("*")) {
              if (element === target || target.contains(element) || element.contains(target)) continue;
              if (element.closest("[data-ios-screen], .page[id], [role=tabpanel][id], [data-screen-id]")) continue;
              const hint = [element.id, ...element.classList].join(" ").toLowerCase();
              if (/statusbar|status-bar|notch|home-indicator/.test(hint)) continue;
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              const rootRect = visualRoot.getBoundingClientRect();
              const interactive = element.matches("button, a, input, select, textarea, [role=button], [role=tab]")
                || Boolean(element.querySelector("button, a, input, select, textarea, [role=button], [role=tab]"));
              const edgeLike = rect.width >= rootRect.width * 0.7 && (
                rect.top <= rootRect.top + Math.max(96, rootRect.height * 0.14)
                || rect.bottom >= rootRect.bottom - Math.max(24, rootRect.height * 0.05)
              );
              const named = /(?:^|[\s_-])(nav|header|footer|bottom|top|toolbar|tabbar|tab-bar|actions?|dock)(?:$|[\s_-])/.test(hint);
              if ((edgeLike && interactive) || named || ["fixed", "sticky"].includes(style.position)) {
                sharedRegionSelectors.push(element);
              }
            }
            const ancestorVisualContext = [];
            let current = target.parentElement;
            while (current && current !== document.documentElement) {
              const style = getComputedStyle(current);
              ancestorVisualContext.push({
                selector: cssPath(current),
                classNames: Array.from(current.classList),
                backgroundColor: style.backgroundColor,
                color: style.color,
                colorScheme: style.colorScheme,
                overflowX: style.overflowX,
                overflowY: style.overflowY,
              });
              if (current === visualRoot) break;
              current = current.parentElement;
            }
            return {
              visualRootSelector: cssPath(visualRoot),
              contentRootSelector: cssPath(target),
              sharedRegionSelectors: sharedRegionSelectors
                .filter((element) => !sharedRegionSelectors.some((candidate) => candidate !== element && candidate.contains(element)))
                .map(cssPath),
              ancestorVisualContext,
              ancestorStateClasses: ancestorVisualContext.flatMap((item) => item.classNames),
            };
          };
          const virtualTargets = new Map();
          const controls = Array.from(document.querySelectorAll("[data-page], [data-screen], [data-route], [aria-controls], [data-ios-action][data-ios-target]"));
          for (const control of controls) {
            const targetHint = control.getAttribute("data-page") || control.getAttribute("data-screen") || control.getAttribute("data-route") || control.getAttribute("aria-controls") || control.getAttribute("data-ios-target");
            const targetId = targetHint ? targetHint.replace(/^#/, "") : null;
            const escapedTarget = targetId ? CSS.escape(targetId) : null;
            const target = targetId ? document.querySelector(`[data-ios-screen="${escapedTarget}"]`) || document.getElementById(targetId) : null;
            if (!target) continue;
            const semanticScreenId = target.getAttribute("data-ios-screen") || target.id;
            const envelope = visualEnvelope(target);
            const existing = virtualTargets.get(semanticScreenId) || {
              id: semanticScreenId,
              rootSelector: target.getAttribute("data-ios-screen") ? `[data-ios-screen="${CSS.escape(semanticScreenId)}"]` : `#${CSS.escape(target.id)}`,
              title: (target.getAttribute("data-ios-screen-title") || control.textContent || target.getAttribute("aria-label") || semanticScreenId).trim().slice(0, 160),
              bodyTextLength: (target.textContent || "").trim().length,
              activationSelectors: [],
              activationSources: [],
              containerSelector: envelope.visualRootSelector,
              visualRootSelector: envelope.visualRootSelector,
              contentRootSelector: envelope.contentRootSelector,
              sharedRegionSelectors: envelope.sharedRegionSelectors,
              ancestorVisualContext: envelope.ancestorVisualContext,
              ancestorStateClasses: envelope.ancestorStateClasses,
              sourceElementId: target.id || null,
              iosStateOwner: target.getAttribute("data-ios-state-owner"),
              iosStateKind: target.getAttribute("data-ios-state-kind"),
              presentationStyle: target.getAttribute("data-ios-presentation-style"),
              visualSnapshot: visualSnapshot(target),
            };
            existing.activationSelectors.push(cssPath(control));
            const sourceScreen = control.closest("[data-ios-screen], .page[id], [role=tabpanel][id], [data-screen-id]");
            existing.activationSources.push({
              selector: cssPath(control),
              sourceScreenHint: sourceScreen
                ? sourceScreen.getAttribute("data-ios-screen") || sourceScreen.id || sourceScreen.getAttribute("data-screen-id")
                : null,
            });
            virtualTargets.set(semanticScreenId, existing);
          }
          for (const target of document.querySelectorAll("[data-ios-screen], .page[id], [role=tabpanel][id], [data-screen-id]")) {
            const targetId = target.getAttribute("data-ios-screen") || target.id || target.getAttribute("data-screen-id");
            if (!targetId || virtualTargets.has(targetId)) continue;
            const envelope = visualEnvelope(target);
            virtualTargets.set(targetId, {
              id: targetId,
              rootSelector: target.getAttribute("data-ios-screen") ? `[data-ios-screen="${CSS.escape(targetId)}"]` : target.id ? `#${CSS.escape(target.id)}` : cssPath(target),
              title: target.getAttribute("data-ios-screen-title") || target.getAttribute("aria-label") || targetId,
              bodyTextLength: (target.textContent || "").trim().length,
              activationSelectors: [],
              activationSources: [],
              containerSelector: envelope.visualRootSelector,
              visualRootSelector: envelope.visualRootSelector,
              contentRootSelector: envelope.contentRootSelector,
              sharedRegionSelectors: envelope.sharedRegionSelectors,
              ancestorVisualContext: envelope.ancestorVisualContext,
              ancestorStateClasses: envelope.ancestorStateClasses,
              initial: target.hasAttribute("data-ios-screen-initial"),
              sourceElementId: target.id || null,
              iosStateOwner: target.getAttribute("data-ios-state-owner"),
              iosStateKind: target.getAttribute("data-ios-state-kind"),
              presentationStyle: target.getAttribute("data-ios-presentation-style"),
              visualSnapshot: visualSnapshot(target),
            });
          }
          return { title: document.title, links, forms, explicit, virtualScreens: Array.from(virtualTargets.values()), bodyTextLength: (document.body?.innerText || "").length };
        });
        const parsed = new URL(actualURL);
        let localPath = null;
        if (localRoot) {
          const decoded = decodeURIComponent(parsed.pathname).replace(/^\/+/, "");
          const candidate = path.resolve(localRoot, decoded || "index.html");
          if (candidate === localRoot || candidate.startsWith(`${localRoot}${path.sep}`)) localPath = candidate;
        }
        screens.push({
          id,
          kind: discovered.virtualScreens.length ? "prototype-document-shell" : "document-screen",
          includeInNativeConversion: !discovered.virtualScreens.length,
          url: actualURL,
          route: `${parsed.pathname}${parsed.search}${parsed.hash}`,
          localPath,
          title: discovered.title,
          bodyTextLength: discovered.bodyTextLength,
        });
        for (const virtual of discovered.virtualScreens) {
          let virtualId = slug(virtual.id);
          if (usedIds.has(virtualId)) virtualId = `${id}-${virtualId}`;
          let suffix = 2;
          const base = virtualId;
          while (usedIds.has(virtualId)) virtualId = `${base}-${suffix++}`;
          usedIds.add(virtualId);
          screens.push({
            id: virtualId,
            kind: "virtual-screen-state",
            includeInNativeConversion: true,
            documentScreenId: id,
            url: actualURL,
            route: `${parsed.pathname}${parsed.search}${parsed.hash}::${virtual.id}`,
            localPath,
            title: virtual.title,
            bodyTextLength: virtual.bodyTextLength,
            rootSelector: virtual.rootSelector,
            containerSelector: virtual.containerSelector,
            visualRootSelector: virtual.visualRootSelector,
            contentRootSelector: virtual.contentRootSelector,
            sharedRegionSelectors: virtual.sharedRegionSelectors,
            ancestorVisualContext: virtual.ancestorVisualContext,
            ancestorStateClasses: virtual.ancestorStateClasses,
            activation: virtual.activationSelectors.length ? { type: "click", selectors: virtual.activationSelectors } : null,
            virtualStateId: virtual.id,
            sourceElementId: virtual.sourceElementId,
            iosStateOwner: virtual.iosStateOwner,
            iosStateKind: virtual.iosStateKind,
            presentationStyle: virtual.presentationStyle,
            visualSnapshot: virtual.visualSnapshot,
            initial: Boolean(virtual.initial),
          });
          for (const activationSource of virtual.activationSources) rawEdges.push({
            sourceURL: actualURL,
            sourceSelector: activationSource.selector,
            sourceScreenHint: activationSource.sourceScreenHint,
            action: "activate-prototype-screen",
            targetHint: virtual.id,
            discoveryOnly: true,
            confidence: 1,
          });
        }

        for (const link of discovered.links) {
          const targetURL = new URL(link.href, actualURL);
          const sameOrigin = targetURL.origin === allowedOrigin;
          const sameDocumentHash = sameOrigin && targetURL.pathname === parsed.pathname && targetURL.search === parsed.search && targetURL.hash && !targetURL.hash.startsWith("#/");
          const action = !sameOrigin ? "open-url" : sameDocumentHash ? "scroll-to" : "push";
          rawEdges.push({
            sourceURL: actualURL,
            sourceSelector: link.selector,
            action,
            targetURL: targetURL.href,
            sameOrigin,
            label: link.text,
            confidence: 0.96,
          });
          if (sameOrigin && action === "push" && !visited.has(targetURL.href)) queue.push(targetURL.href);
        }
        for (const form of discovered.forms) rawEdges.push({ sourceURL: actualURL, sourceSelector: form.selector, action: "submit", targetURL: form.action, method: form.method, confidence: 0.95 });
        for (const item of discovered.explicit) rawEdges.push({ sourceURL: actualURL, sourceSelector: item.selector, sourceScreenHint: item.sourceScreenHint, action: item.action, targetHint: item.target, presentationStyle: item.presentationStyle, confidence: 1 });
      } catch (error) {
        warnings.push(`Unable to inspect route ${normalizedURL}: ${error.message}`);
      } finally {
        await page.close();
      }
    }
  } finally {
    await context.close();
    await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
  }
  if (queue.length) warnings.push(`Route discovery stopped at --max-pages ${args.maxPages}.`);
  const representationClassification = classifyScreenRepresentations(screens);
  warnings.push(...representationClassification.warnings);
  const visualStates = representationClassification.visualStates;
  const screenByHint = new Map();
  for (const screen of screens) {
    const nativeScreenId = screen.nativeOwnerScreenId || screen.id;
    screenByHint.set(screen.id, nativeScreenId);
    screenByHint.set(screen.route, nativeScreenId);
    if (screen.rootSelector) screenByHint.set(screen.rootSelector, nativeScreenId);
    if (screen.virtualStateId) screenByHint.set(screen.virtualStateId, nativeScreenId);
    if (screen.title) screenByHint.set(screen.title, nativeScreenId);
  }
  const mappedEdges = rawEdges.map((edge) => {
    const representation = edge.targetHint
      ? screens.find((screen) =>
          screen.id === edge.targetHint
          || screen.virtualStateId === edge.targetHint
          || screen.route === edge.targetHint
        )
      : null;
    const targetScreenId = edge.targetURL
      ? idByURL.get(edge.targetURL) || null
      : screenByHint.get(edge.targetHint) || null;
    const expectsScreen = edge.sameOrigin === true && !["scroll-to", "submit"].includes(edge.action)
      || Boolean(edge.targetHint && !["open-url", "scroll-to", "submit"].includes(edge.action));
    const unresolvedTarget = expectsScreen && !targetScreenId
      ? edge.targetHint || edge.targetURL || null
      : null;
    const targetAnchor = edge.action === "scroll-to" && edge.targetURL
      ? new URL(edge.targetURL).hash || null
      : null;
    const externalURL = edge.action === "open-url" ? edge.targetURL || edge.targetHint || null : null;
    const sourceScreenId = screenByHint.get(edge.sourceScreenHint) || idByURL.get(edge.sourceURL) || null;
    const sourceRoute = screens.find((screen) => screen.id === sourceScreenId)?.route || null;
    const targetRoute = screens.find((screen) => screen.id === targetScreenId)?.route || null;
    const { sameOrigin, sourceScreenHint, ...publicEdge } = edge;
    const stateRepresentation = representation?.stateRepresentation || null;
    const stateAction = stateRepresentation
      ? stateRepresentation.kind === "presentation"
        ? `present-${stateRepresentation.presentationStyle || "overlay"}`
        : stateRepresentation.localEffect === "swipe-actions"
          ? "reveal-swipe-actions"
          : "update-local-state"
      : null;
    return {
      sourceScreenId,
      sourceRoute,
      targetScreenId,
      targetStateId: representation?.stateRepresentation?.id || null,
      targetRoute,
      unresolvedTarget,
      targetAnchor,
      externalURL,
      ...publicEdge,
      action: stateAction || publicEdge.action,
      discoveryOnly: stateRepresentation ? false : publicEdge.discoveryOnly,
    };
  });
  const seenEdges = new Set();
  const edges = mappedEdges
    .filter((edge) => {
      const key = JSON.stringify([
        edge.sourceScreenId, edge.sourceSelector, edge.action, edge.targetScreenId,
        edge.targetStateId, edge.targetAnchor, edge.externalURL,
      ]);
      if (seenEdges.has(key)) return false;
      seenEdges.add(key);
      return true;
    })
    .map((edge, index) => ({ id: `route-${index + 1}`, ...edge }));
  const output = {
    schemaVersion: "html-route-graph-1.0",
    source: args.html ? { kind: "html-project", entry: path.resolve(args.html), root: localRoot } : { kind: "url", entry: args.url },
    entryScreenId: idByURL.get(entryURL) || screens[0]?.id || null,
    screens,
    visualStates,
    edges,
    warnings: Array.from(new Set(warnings)),
  };
  const outPath = path.resolve(args.out);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify({ out: outPath, screens: screens.length, nativeScreens: screens.filter((screen) => screen.includeInNativeConversion !== false).length, edges: edges.length, unresolvedEdges: edges.filter((edge) => edge.unresolvedTarget).length, warnings: output.warnings.length }, null, 2)}\n`);
}

main().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
