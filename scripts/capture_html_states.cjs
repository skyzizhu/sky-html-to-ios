#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { fileURLToPath, pathToFileURL } = require("url");

function parseArgs(argv) {
  const result = { timeout: 15000, allowRemote: false };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--allow-remote") { result.allowRemote = true; continue; }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (value === undefined) throw new Error(`Missing value for ${key}`);
    result[key.slice(2).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
    index += 1;
  }
  if (!result.manifest || !result.outDir) throw new Error("--manifest and --out-dir are required");
  result.timeout = Number(result.timeout);
  return result;
}

function contentType(filePath) {
  return ({
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".woff": "font/woff", ".woff2": "font/woff2",
  })[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

function createStaticServer(entryPath) {
  const root = path.dirname(entryPath);
  const server = http.createServer((request, response) => {
    const requestURL = new URL(request.url, "http://127.0.0.1");
    const decoded = decodeURIComponent(requestURL.pathname).replace(/^\/+/, "");
    const candidate = path.resolve(root, decoded || path.basename(entryPath));
    if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return response.writeHead(403).end("Forbidden");
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

async function performAction(page, action) {
  const selector = action.selector ? action.selector.replaceAll(" >>> ", " ") : action.selector;
  switch (action.type) {
    case "click": {
      const locator = page.locator(selector).first();
      if (action.purpose === "activate-screen") await locator.evaluate((element) => element.click());
      else await locator.click();
      break;
    }
    case "fill": await page.locator(selector).fill(String(action.value ?? "")); break;
    case "check": await page.locator(selector).check(); break;
    case "uncheck": await page.locator(selector).uncheck(); break;
    case "select": await page.locator(selector).selectOption(action.value); break;
    case "press": await page.locator(selector).press(action.key); break;
    case "hover": await page.locator(selector).hover(); break;
    case "wait": await page.waitForTimeout(Number(action.ms || 100)); break;
    case "scroll": {
      await page.locator(selector || "html").evaluate((element, position) => {
        const max = Math.max(0, element.scrollHeight - element.clientHeight);
        const top = position === "bottom" ? max : position === "middle" ? max / 2 : 0;
        element.scrollTo({ top, left: 0, behavior: "instant" });
      }, action.position || "top");
      break;
    }
    default: throw new Error(`Unsupported action type: ${action.type}`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const manifest = JSON.parse(fs.readFileSync(path.resolve(args.manifest), "utf8"));
  if (manifest.schemaVersion !== "visual-state-manifest-1.0") throw new Error("Unsupported visual state manifest");
  const playwright = require("playwright");
  const sharp = require("sharp");
  const source = manifest.source || {};
  let server = null;
  let localRoot = null;
  let targetURL = source.url;
  const warnings = [];
  if (source.html) {
    const entry = path.resolve(source.html);
    localRoot = path.dirname(entry);
    try {
      const hosted = await createStaticServer(entry);
      server = hosted.server;
      targetURL = hosted.url;
    } catch (error) {
      targetURL = pathToFileURL(entry).href;
      warnings.push(`Local HTTP server unavailable; fell back to file URL: ${error.code || error.message}`);
    }
  }
  if (!targetURL) throw new Error("Manifest source must provide html or url");
  const browser = await playwright.chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: Number(manifest.viewport.width), height: Number(manifest.viewport.height) },
    deviceScaleFactor: 1,
    colorScheme: manifest.appearance === "dark" ? "dark" : "light",
    reducedMotion: "no-preference",
  });
  const allowedOrigin = new URL(targetURL).origin;
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
  const outDir = path.resolve(args.outDir);
  fs.mkdirSync(outDir, { recursive: true });
  const captures = [];
  try {
    for (const state of manifest.states || []) {
      const page = await context.newPage();
      await page.goto(targetURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
      await page.evaluate(async () => { if (document.fonts?.ready) await document.fonts.ready; });
      for (const action of state.htmlActions || []) await performAction(page, action);
      if (state.animationProgress != null) {
        await page.evaluate((progress) => {
          for (const animation of document.getAnimations()) {
            const timing = animation.effect?.getComputedTiming();
            const endTime = Number(timing?.endTime);
            const duration = Number(timing?.duration);
            const sampleDuration = Number.isFinite(endTime) ? endTime : duration;
            animation.pause();
            if (Number.isFinite(sampleDuration)) animation.currentTime = Math.max(0, Math.min(1, progress)) * sampleDuration;
          }
        }, Number(state.animationProgress));
      }
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const screenshot = path.join(outDir, `${state.id}.png`);
      const root = page.locator(manifest.rootSelector || "html");
      const screenshotBuffer = await root.screenshot({ animations: state.animationProgress == null ? "disabled" : "allow" });
      const originalMetadata = await sharp(screenshotBuffer).metadata();
      const targetViewport = manifest.targetViewport || {};
      const targetWidth = Number(targetViewport.width);
      const targetHeight = Number(targetViewport.height);
      const shouldNormalize = Number.isFinite(targetWidth) && Number.isFinite(targetHeight) && targetWidth > 0 && targetHeight > 0
        && (originalMetadata.width !== targetWidth || originalMetadata.height !== targetHeight);
      if (shouldNormalize) {
        await sharp(screenshotBuffer).resize(Math.round(targetWidth), Math.round(targetHeight), {
          fit: manifest.normalization?.mode || "cover",
          position: manifest.normalization?.position || "centre",
        }).png().toFile(screenshot);
      } else {
        fs.writeFileSync(screenshot, screenshotBuffer);
      }
      captures.push({
        id: state.id,
        screenshot,
        actions: state.htmlActions || [],
        animationProgress: state.animationProgress,
        originalSize: { width: originalMetadata.width, height: originalMetadata.height },
        outputSize: shouldNormalize ? { width: Math.round(targetWidth), height: Math.round(targetHeight) } : { width: originalMetadata.width, height: originalMetadata.height },
        normalized: shouldNormalize,
        normalization: shouldNormalize ? manifest.normalization : null,
      });
      await page.close();
    }
  } finally {
    await context.close();
    await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
  }
  const report = { schemaVersion: "html-state-captures-1.0", manifest: path.resolve(args.manifest), captures, warnings };
  fs.writeFileSync(path.join(outDir, "captures.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify({ outDir, captures: captures.length }, null, 2)}\n`);
}

main().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
