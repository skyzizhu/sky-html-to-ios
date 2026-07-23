#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { fileURLToPath, pathToFileURL } = require("url");

function parseArgs(argv) {
  const result = { width: 393, height: 852, timeout: 15000, waitMs: 180, screenId: "screen", allowRemote: false };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--allow-remote") { result.allowRemote = true; continue; }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    if (argv[index + 1] == null) throw new Error(`Missing value for ${key}`);
    result[key.slice(2).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = argv[index + 1];
    index += 1;
  }
  if (Boolean(result.html) === Boolean(result.url)) throw new Error("Provide exactly one of --html or --url");
  if (!result.out) throw new Error("--out is required");
  for (const key of ["width", "height", "timeout", "waitMs"]) result[key] = Number(result[key]);
  return result;
}

function createStaticServer(entryPath) {
  const root = path.dirname(entryPath);
  const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf" };
  const server = http.createServer((request, response) => {
    const requestURL = new URL(request.url, "http://127.0.0.1");
    const relative = decodeURIComponent(requestURL.pathname).replace(/^\/+/, "") || path.basename(entryPath);
    let candidate = path.resolve(root, relative);
    if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return response.writeHead(403).end("Forbidden");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) candidate = path.join(candidate, "index.html");
    if (!fs.existsSync(candidate)) return response.writeHead(404).end("Not found");
    response.writeHead(200, { "Content-Type": `${types[path.extname(candidate).toLowerCase()] || "application/octet-stream"}; charset=utf-8`, "Cache-Control": "no-store" });
    fs.createReadStream(candidate).pipe(response);
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/${encodeURIComponent(path.basename(entryPath))}` }));
  });
}

function classify(samples, maxScroll, edge) {
  if (maxScroll < 8 || samples.length < 2) return { behavior: "fixed", confidence: 0.55, evidence: ["No meaningful scroll range; geometry remained at its initial edge."] };
  const first = samples[0];
  const last = samples[samples.length - 1];
  const yRange = Math.max(...samples.map((item) => item.y)) - Math.min(...samples.map((item) => item.y));
  const heightRange = Math.max(...samples.map((item) => item.height)) - Math.min(...samples.map((item) => item.height));
  const opacityRange = Math.max(...samples.map((item) => item.opacity)) - Math.min(...samples.map((item) => item.opacity));
  const colorChanged = samples.some((item) => item.backgroundColor !== first.backgroundColor);
  const offscreen = edge === "top" ? last.bottom <= 1 : last.y >= last.viewportHeight - 1;
  if (heightRange >= Math.max(8, first.height * 0.18)) return { behavior: "collapse", confidence: 0.9, evidence: [`Height changed by ${heightRange.toFixed(1)}px while scrolling.`] };
  if (opacityRange >= 0.45 || offscreen || last.visibility === "hidden") return { behavior: "hide-on-scroll", confidence: 0.88, evidence: ["Region became transparent, hidden, or moved outside the viewport."] };
  if (yRange <= 2.5) {
    if (colorChanged || opacityRange >= 0.08) return { behavior: "appearance-change", confidence: 0.82, evidence: ["Geometry stayed fixed while visual appearance changed."] };
    return { behavior: "fixed", confidence: 0.92, evidence: ["Viewport position remained stable across scroll samples."] };
  }
  const delta = last.y - first.y;
  if (Math.abs(delta + maxScroll) <= Math.max(12, maxScroll * 0.18)) return { behavior: "scroll-away", confidence: 0.9, evidence: ["Region moved with document content rather than staying viewport-pinned."] };
  const tail = samples.slice(-2);
  if (tail.length === 2 && Math.abs(tail[1].y - tail[0].y) <= 2.5) return { behavior: "sticky", confidence: 0.84, evidence: ["Region moved initially and then stabilized at an edge."] };
  return { behavior: "unknown", confidence: 0.45, evidence: ["Observed motion did not match a deterministic native scrolling pattern."] };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { chromium } = require("playwright");
  let server = null;
  let entryURL = args.url;
  let localRoot = null;
  if (args.html) {
    const entry = path.resolve(args.html);
    localRoot = path.dirname(entry);
    try { ({ server, url: entryURL } = await createStaticServer(entry)); }
    catch (_) { entryURL = pathToFileURL(entry).href; }
  }
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: args.width, height: args.height }, deviceScaleFactor: 1 });
  const origin = new URL(entryURL).origin;
  if (!args.allowRemote) await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    let allowed = ["data:", "blob:"].includes(url.protocol) || url.origin === origin;
    if (url.protocol === "file:" && localRoot) {
      const requestedPath = path.resolve(fileURLToPath(url));
      allowed = requestedPath === localRoot || requestedPath.startsWith(`${localRoot}${path.sep}`);
    }
    if (allowed) await route.continue(); else await route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  try {
    await page.goto(entryURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
    await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
    if (args.activateSelector) {
      const activator = page.locator(args.activateSelector).first();
      if (await activator.count()) await activator.evaluate((element) => element.click());
      else throw new Error(`Activation selector did not match: ${args.activateSelector}`);
    }
    await page.waitForTimeout(args.waitMs);
    const setup = await page.evaluate((rootSelector) => {
      const root = rootSelector ? document.querySelector(rootSelector) : document.body;
      if (!root) throw new Error(`Root selector did not match: ${rootSelector}`);
      const cssPath = (element) => {
        if (element.id) return `#${CSS.escape(element.id)}`;
        const parts = [];
        for (let current = element; current && current !== root && parts.length < 5; current = current.parentElement) {
          let part = current.tagName.toLowerCase();
          const siblings = current.parentElement ? Array.from(current.parentElement.children).filter((item) => item.tagName === current.tagName) : [];
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
          parts.unshift(part);
        }
        return parts.join(" > ");
      };
      const scrollables = [root, ...root.querySelectorAll("*")].filter((element) => {
        const style = getComputedStyle(element);
        return /(auto|scroll)/.test(style.overflowY) && element.scrollHeight > element.clientHeight + 8;
      });
      const scrollRoot = scrollables.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight))[0] || document.scrollingElement;
      const rootRect = root.getBoundingClientRect();
      const candidates = Array.from(root.querySelectorAll("header,nav,footer,[role=navigation],[data-ios-region],body *")).filter((element) => {
        const rect = element.getBoundingClientRect();
        if (rect.width < rootRect.width * 0.7 || rect.height < 24 || rect.height > rootRect.height * 0.25) return false;
        const style = getComputedStyle(element);
        const nearTop = rect.top <= rootRect.top + rootRect.height * 0.16;
        const nearBottom = rect.bottom >= rootRect.bottom - rootRect.height * 0.16;
        const name = `${element.tagName} ${element.id} ${element.className} ${element.getAttribute("role") || ""}`;
        const semantic = /^(HEADER|NAV|FOOTER)$/.test(element.tagName) || element.hasAttribute("data-ios-region") || /(nav|header|footer|top.?bar|bottom.?bar|toolbar|action.?bar)/i.test(name);
        const viewportPinned = ["fixed", "sticky"].includes(style.position);
        const structural = element.children.length >= 2;
        return viewportPinned || ((nearTop || nearBottom) && (semantic || structural));
      }).slice(0, 30);
      window.__htmlToIOSScrollProbe = {
        root, scrollRoot,
        candidates: candidates.map((element, index) => ({
          element, key: element.dataset.iosNodeId || element.id || `candidate-${index + 1}`,
          selector: cssPath(element),
          edge: element.getBoundingClientRect().top - rootRect.top < rootRect.bottom - element.getBoundingClientRect().bottom ? "top" : "bottom",
        })),
      };
      return { maxScroll: Math.max(0, scrollRoot.scrollHeight - scrollRoot.clientHeight), candidateCount: candidates.length };
    }, args.selector || null);
    const samplesByKey = {};
    for (const ratio of [0, 0.25, 0.5, 1]) {
      const samples = await page.evaluate(async ({ ratio, waitMs }) => {
        const probe = window.__htmlToIOSScrollProbe;
        const maxScroll = Math.max(0, probe.scrollRoot.scrollHeight - probe.scrollRoot.clientHeight);
        if (probe.scrollRoot === document.scrollingElement) window.scrollTo(0, maxScroll * ratio);
        else probe.scrollRoot.scrollTop = maxScroll * ratio;
        await new Promise((resolve) => setTimeout(resolve, waitMs));
        return probe.candidates.map((candidate) => {
          const rect = candidate.element.getBoundingClientRect();
          const style = getComputedStyle(candidate.element);
          return { key: candidate.key, selector: candidate.selector, edge: candidate.edge, ratio, scrollOffset: maxScroll * ratio, y: rect.y, bottom: rect.bottom, width: rect.width, height: rect.height, opacity: Number(style.opacity), visibility: style.visibility, display: style.display, transform: style.transform, backgroundColor: style.backgroundColor, viewportHeight: innerHeight };
        });
      }, { ratio, waitMs: args.waitMs });
      for (const sample of samples) (samplesByKey[sample.key] ||= []).push(sample);
    }
    const regions = Object.entries(samplesByKey).map(([nodeId, samples]) => {
      const result = classify(samples, setup.maxScroll, samples[0].edge);
      return { nodeId, selector: samples[0].selector, edge: samples[0].edge, ...result, samples };
    });
    const report = { schemaVersion: "scroll-region-behavior-1.0", screenId: String(args.screenId), viewport: { width: args.width, height: args.height }, maxScroll: setup.maxScroll, regions };
    fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true });
    fs.writeFileSync(args.out, `${JSON.stringify(report, null, 2)}\n`);
    process.stdout.write(`${JSON.stringify({ out: path.resolve(args.out), screenId: args.screenId, regions: regions.length }, null, 2)}\n`);
  } finally {
    await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => { console.error(error.stack || String(error)); process.exit(1); });
