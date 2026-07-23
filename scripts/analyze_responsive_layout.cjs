#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { fileURLToPath, pathToFileURL } = require("url");

function parseArgs(argv) {
  const result = { widths: "320,375,393,430", height: 852, baselineWidth: 393, mode: "auto", timeout: 15000, allowRemote: false };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--allow-remote") { result.allowRemote = true; continue; }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (value == null) throw new Error(`Missing value for ${key}`);
    result[key.slice(2).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
    index += 1;
  }
  if (Boolean(result.html) === Boolean(result.url)) throw new Error("Provide exactly one of --html or --url");
  if (!result.out) throw new Error("--out is required");
  result.widths = String(result.widths).split(",").map(Number).filter((value) => Number.isFinite(value) && value > 0);
  result.height = Number(result.height);
  result.baselineWidth = Number(result.baselineWidth);
  result.timeout = Number(result.timeout);
  if (!result.widths.length) throw new Error("--widths must contain positive numbers");
  if (!["auto", "viewport", "fixed-artboard"].includes(result.mode)) throw new Error("--mode must be auto, viewport, or fixed-artboard");
  return result;
}

function createStaticServer(entryPath) {
  const root = path.dirname(entryPath);
  const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf" };
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

function range(values) { return Math.max(...values) - Math.min(...values); }
function stable(values, tolerance = 1.5) { return range(values) <= tolerance; }
function average(values) { return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length); }
function near(value, target, tolerance = 0.035) { return Math.abs(value - target) <= tolerance; }

function inferRule(entries) {
  const left = entries.map((entry) => entry.relative.left);
  const right = entries.map((entry) => entry.relative.right);
  const widths = entries.map((entry) => entry.rect.width);
  const parentWidths = entries.map((entry) => entry.parentRect.width);
  const centers = entries.map((entry) => entry.relative.left + entry.rect.width / 2 - entry.parentRect.width / 2);
  const widthRatios = entries.map((entry) => entry.rect.width / Math.max(1, entry.parentRect.width));
  let horizontal;
  if (stable(left) && stable(right)) horizontal = "leading-trailing";
  else if (stable(left) && stable(widths)) horizontal = "leading-fixed-width";
  else if (stable(right) && stable(widths)) horizontal = "trailing-fixed-width";
  else if (stable(centers) && stable(widths)) horizontal = "center-fixed-width";
  else if (stable(widths) && stable(entries.map((entry) => Math.abs(entry.relative.left - entry.relative.right)), 2)) horizontal = "centered-max-width";
  else if (stable(widthRatios, 0.025)) horizontal = "proportional-width";
  else horizontal = "intrinsic-or-custom";

  const heights = entries.map((entry) => entry.rect.height);
  const top = entries.map((entry) => entry.relative.top);
  const bottom = entries.map((entry) => entry.relative.bottom);
  let vertical;
  if (stable(top) && stable(bottom)) vertical = "top-bottom";
  else if (stable(top) && stable(heights)) vertical = "top-fixed-height";
  else if (stable(bottom) && stable(heights)) vertical = "bottom-fixed-height";
  else if (stable(heights)) vertical = "content-or-fixed-height";
  else vertical = "content-driven-height";

  const parentDelta = parentWidths[parentWidths.length - 1] - parentWidths[0];
  const widthDelta = widths[widths.length - 1] - widths[0];
  return {
    horizontal,
    vertical,
    baseline: {
      leading: left[Math.floor(left.length / 2)],
      trailing: right[Math.floor(right.length / 2)],
      width: widths[Math.floor(widths.length / 2)],
      height: heights[Math.floor(heights.length / 2)],
    },
    response: {
      widthPerParentWidth: Math.abs(parentDelta) > 0.1 ? widthDelta / parentDelta : 0,
      averageWidthRatio: average(widthRatios),
      behavesAsStretch: Math.abs(parentDelta) > 0.1 && near(widthDelta / parentDelta, 1, 0.08),
    },
    autoLayoutGuidance: horizontal === "leading-trailing" ? "Pin leading and trailing; do not set a fixed width."
      : horizontal === "centered-max-width" ? "Center horizontally and constrain width <= baseline width."
      : horizontal === "proportional-width" ? "Use a proportional width constraint only because the ratio remained stable across probes."
      : horizontal.includes("fixed-width") ? "Keep intrinsic/fixed width and apply the named anchor."
      : "Prefer intrinsic size and constraint priorities; review this node manually.",
  };
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
  const browser = await chromium.launch({ headless: true });
  const origin = new URL(entryURL).origin;

  async function capture(targetWidth, sourceProbeWidth = null, screenshotPath = null) {
    const context = await browser.newContext({ viewport: { width: Math.round(targetWidth), height: args.height }, deviceScaleFactor: 1 });
    if (!args.allowRemote) await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      let allowed = ["data:", "blob:"].includes(url.protocol) || url.origin === origin;
      if (url.protocol === "file:" && localRoot) {
        const requestedPath = path.resolve(fileURLToPath(url));
        allowed = requestedPath === localRoot || requestedPath.startsWith(`${localRoot}${path.sep}`);
      }
      if (allowed) await route.continue();
      else await route.abort("blockedbyclient");
    });
    const page = await context.newPage();
    await page.goto(entryURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
    await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
    let selector = args.selector;
    if (!selector) selector = await page.evaluate(() => {
      const candidates = Array.from(document.querySelectorAll("[id], [class]")).map((element) => {
        const rect = element.getBoundingClientRect();
        const name = `${element.id} ${element.className}`;
        const score = (rect.width >= 280 && rect.width <= 500 ? 2 : 0) + (rect.height / Math.max(1, rect.width) > 1.5 ? 2 : 0) + (/(phone|screen|mobile|app)/i.test(name) ? 4 : 0);
        return { element, score, area: rect.width * rect.height };
      }).filter((item) => item.score >= 6).sort((a, b) => b.score - a.score || b.area - a.area);
      const element = candidates[0]?.element || document.body;
      return element.id ? `#${CSS.escape(element.id)}` : "body";
    });
    if (args.activateSelector) {
      const activator = page.locator(args.activateSelector).first();
      if (await activator.count()) await activator.evaluate((element) => element.click());
      else warnings.push(`Activation selector not found: ${args.activateSelector}`);
    }
    await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
    if (sourceProbeWidth != null) await page.evaluate(({ selector, width }) => {
      const root = document.querySelector(selector);
      if (!root) throw new Error(`Root selector did not match: ${selector}`);
      root.style.setProperty("width", `${width}px`, "important");
      root.style.setProperty("min-width", "0", "important");
      root.style.setProperty("max-width", "none", "important");
      root.style.setProperty("flex", "0 0 auto", "important");
    }, { selector, width: sourceProbeWidth });
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const measured = await page.evaluate((selector) => {
      const root = document.querySelector(selector);
      if (!root) throw new Error(`Root selector did not match: ${selector}`);
      const cssPath = (element) => {
        if (element.id) return `#${CSS.escape(element.id)}`;
        const parts = [];
        let current = element;
        while (current && current !== root && parts.length < 6) {
          let part = current.tagName.toLowerCase();
          const classes = Array.from(current.classList).slice(0, 2);
          if (classes.length) part += classes.map((name) => `.${CSS.escape(name)}`).join("");
          const siblings = current.parentElement ? Array.from(current.parentElement.children).filter((item) => item.tagName === current.tagName) : [];
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
          parts.unshift(part);
          current = current.parentElement;
        }
        return parts.join(" > ") || selector;
      };
      const elements = [root, ...root.querySelectorAll("*")];
      return {
        selector,
        nodes: elements.map((element, index) => {
          const rect = element.getBoundingClientRect();
          const parent = element === root ? null : element.parentElement;
          const parentRect = parent ? parent.getBoundingClientRect() : rect;
          const style = getComputedStyle(element);
          let lineCount = null;
          const text = (element.innerText || "").trim();
          if (text && Array.from(element.childNodes).some((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim())) {
            const range = document.createRange(); range.selectNodeContents(element);
            const tops = [];
            for (const item of range.getClientRects()) if (!tops.some((top) => Math.abs(top - item.top) <= 1.5)) tops.push(item.top);
            lineCount = tops.length;
          }
          return {
            id: element.getAttribute("data-ios-node-id") || element.id || cssPath(element) || `node-${index + 1}`,
            parentId: parent ? parent.getAttribute("data-ios-node-id") || parent.id || cssPath(parent) : null,
            selector: cssPath(element),
            visible: style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0,
            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
            parentRect: { x: parentRect.x, y: parentRect.y, width: parentRect.width, height: parentRect.height },
            relative: { left: rect.left - parentRect.left, right: parentRect.right - rect.right, top: rect.top - parentRect.top, bottom: parentRect.bottom - rect.bottom },
            style: { display: style.display, position: style.position, width: style.width, maxWidth: style.maxWidth, minWidth: style.minWidth, flexGrow: style.flexGrow, flexShrink: style.flexShrink },
            lineCount,
          };
        }),
      };
    }, selector);
    if (screenshotPath) {
      if (sourceProbeWidth != null) await page.evaluate(({ selector, scale }) => {
        const root = document.querySelector(selector);
        root.style.setProperty("transform-origin", "top left", "important");
        root.style.setProperty("transform", `scale(${scale})`, "important");
      }, { selector, scale: targetWidth / sourceProbeWidth });
      fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
      await page.locator(selector).screenshot({ path: screenshotPath, animations: "disabled" });
    }
    await context.close();
    return { targetWidth, sourceProbeWidth: sourceProbeWidth || targetWidth, ...measured };
  }

  try {
    const natural = [];
    for (const width of args.widths) natural.push(await capture(width));
    const naturalRootWidths = natural.map((sample) => sample.nodes[0].rect.width);
    const targetSpan = Math.max(...args.widths) - Math.min(...args.widths);
    const rootSpan = Math.max(...naturalRootWidths) - Math.min(...naturalRootWidths);
    const selectedMode = args.mode === "auto" ? (rootSpan >= targetSpan * 0.6 ? "viewport" : "fixed-artboard") : args.mode;
    const baselineIndex = args.widths.reduce((best, width, index) => Math.abs(width - args.baselineWidth) < Math.abs(args.widths[best] - args.baselineWidth) ? index : best, 0);
    const sourceNaturalWidth = naturalRootWidths[baselineIndex];
    const designScale = selectedMode === "fixed-artboard" ? args.baselineWidth / sourceNaturalWidth : 1;
    let samples = natural;
    if (selectedMode === "fixed-artboard") {
      samples = [];
      for (const width of args.widths) {
        const screenshot = args.screenshotDir ? path.resolve(args.screenshotDir, `${width}.png`) : null;
        samples.push(await capture(width, width / designScale, screenshot));
      }
    } else if (args.screenshotDir) {
      samples = [];
      for (const width of args.widths) samples.push(await capture(width, null, path.resolve(args.screenshotDir, `${width}.png`)));
    }

    for (const sample of samples) {
      for (const node of sample.nodes) {
        for (const key of ["rect", "parentRect", "relative"]) {
          for (const field of Object.keys(node[key])) node[key][field] *= designScale;
        }
      }
    }
    const ids = samples.map((sample) => new Set(sample.nodes.filter((node) => node.visible).map((node) => node.id)));
    const common = [...ids[0]].filter((id) => ids.every((set) => set.has(id)));
    const rules = common.map((id) => {
      const entries = samples.map((sample) => sample.nodes.find((node) => node.id === id));
      return { nodeId: id, selector: entries[0].selector, parentId: entries[0].parentId, ...inferRule(entries), lineCounts: entries.map((entry, index) => ({ width: args.widths[index], count: entry.lineCount })) };
    });
    const output = {
      schemaVersion: "responsive-layout-analysis-1.0",
      source: args.html ? { kind: "html-file", entry: path.resolve(args.html) } : { kind: "url", entry: args.url },
      rootSelector: samples[0].selector,
      mode: selectedMode,
      normalization: {
        policy: selectedMode === "fixed-artboard" ? "scale-design-tokens-once-then-use-auto-layout" : "one-css-pixel-to-one-point-at-baseline",
        sourceNaturalWidthCssPx: sourceNaturalWidth,
        baselineTargetWidthPt: args.baselineWidth,
        designScale,
        runtimeWholePageScalingAllowed: false,
      },
      sampleWidthsPt: args.widths,
      samples: samples.map((sample) => ({ targetWidthPt: sample.targetWidth, sourceProbeWidthCssPx: sample.sourceProbeWidth, rootWidthPt: sample.nodes[0].rect.width })),
      rules,
      warnings: Array.from(new Set(warnings)),
      summary: { nodesCompared: rules.length, fixedArtboardDetected: selectedMode === "fixed-artboard", ambiguousNodes: rules.filter((rule) => rule.horizontal === "intrinsic-or-custom").length },
    };
    const out = path.resolve(args.out);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, `${JSON.stringify(output, null, 2)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify({ out, mode: selectedMode, ...output.normalization, ...output.summary }, null, 2)}\n`);
  } finally {
    await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
