#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { fileURLToPath, pathToFileURL } = require("url");

function parseArgs(argv) {
  const result = { width: 393, height: 852, timeout: 15000, waitMs: 100, activateWaitMs: 1500, allowRemote: false, captureMotion: true };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--allow-remote") {
      result.allowRemote = true;
      continue;
    }
    if (key === "--skip-motion") {
      result.captureMotion = false;
      continue;
    }
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (value === undefined) throw new Error(`Missing value for ${key}`);
    result[key.slice(2).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
    index += 1;
  }
  for (const key of ["width", "height", "timeout", "waitMs", "activateWaitMs"]) {
    result[key] = Number(result[key]);
    if (!Number.isFinite(result[key]) || result[key] < 0) throw new Error(`Invalid --${key}: ${result[key]}`);
  }
  if (Boolean(result.html) === Boolean(result.url)) {
    throw new Error("Provide exactly one of --html or --url");
  }
  if (!result.out) throw new Error("--out is required");
  return result;
}

function contentType(filePath) {
  const types = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".mp4": "video/mp4", ".webm": "video/webm",
  };
  return types[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

function createStaticServer(entryPath) {
  const root = path.dirname(entryPath);
  const server = http.createServer((request, response) => {
    try {
      const requestURL = new URL(request.url, "http://127.0.0.1");
      const decoded = decodeURIComponent(requestURL.pathname).replace(/^\/+/, "");
      const candidate = path.resolve(root, decoded || path.basename(entryPath));
      if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) {
        response.writeHead(403).end("Forbidden");
        return;
      }
      let target = candidate;
      if (fs.existsSync(target) && fs.statSync(target).isDirectory()) target = path.join(target, "index.html");
      if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
        response.writeHead(404).end("Not found");
        return;
      }
      response.writeHead(200, { "Content-Type": contentType(target), "Cache-Control": "no-store" });
      fs.createReadStream(target).pipe(response);
    } catch (error) {
      response.writeHead(500).end(String(error));
    }
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const relativeEntry = path.relative(root, entryPath).split(path.sep).map(encodeURIComponent).join("/");
      resolve({ server, url: `http://127.0.0.1:${address.port}/${relativeEntry}` });
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let playwright;
  try {
    playwright = require("playwright");
  } catch (error) {
    throw new Error("Playwright is unavailable. Set NODE_PATH to a node_modules directory containing playwright.");
  }

  let server = null;
  let targetURL = args.url;
  let localRoot = null;
  const startupWarnings = [];
  if (args.html) {
    const entry = path.resolve(args.html);
    if (!fs.existsSync(entry) || !fs.statSync(entry).isFile()) throw new Error(`HTML entry does not exist: ${entry}`);
    localRoot = path.dirname(entry);
    try {
      const hosted = await createStaticServer(entry);
      server = hosted.server;
      targetURL = hosted.url;
    } catch (error) {
      targetURL = pathToFileURL(entry).href;
      startupWarnings.push(`Local HTTP server unavailable; fell back to file URL: ${error.code || error.message}`);
    }
  }

  const warnings = [...startupWarnings];
  let browser = null;
  try {
    browser = await playwright.chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
      deviceScaleFactor: 1,
      colorScheme: args.appearance === "dark" ? "dark" : "light",
      reducedMotion: "no-preference",
    });
    const page = await context.newPage();
    const allowedOrigin = new URL(targetURL).origin;
    if (!args.allowRemote) {
      await page.route("**/*", async (route) => {
        const requestURL = new URL(route.request().url());
        let allowed = requestURL.protocol === "data:" || requestURL.protocol === "blob:" || requestURL.origin === allowedOrigin;
        if (requestURL.protocol === "file:" && localRoot) {
          const requestedPath = path.resolve(fileURLToPath(requestURL));
          allowed = requestedPath === localRoot || requestedPath.startsWith(`${localRoot}${path.sep}`);
        }
        if (allowed) await route.continue();
        else {
          warnings.push(`Blocked remote request: ${requestURL.href}`);
          await route.abort("blockedbyclient");
        }
      });
    }
    await page.goto(targetURL, { waitUntil: "domcontentloaded", timeout: args.timeout });
    try {
      await page.waitForLoadState("networkidle", { timeout: args.timeout });
    } catch (_) {
      warnings.push(`Network idle was not reached within ${args.timeout}ms.`);
    }
    await page.evaluate(async () => {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    if (args.activateSelector) {
      const activator = page.locator(args.activateSelector).first();
      if (!await activator.count()) throw new Error(`Activation selector did not match: ${args.activateSelector}`);
      try {
        await activator.click({ timeout: Math.min(args.timeout, 5000) });
      } catch (error) {
        await activator.evaluate((element) => element.click());
        warnings.push(`Activation used DOM click fallback for ${args.activateSelector}: ${error.message.split("\n")[0]}`);
      }
      if (args.activateWaitMs) await page.waitForTimeout(args.activateWaitMs);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    }
    const rootSelector = args.selector || "html";
    const motions = args.captureMotion ? await page.evaluate((selector) => {
      const root = document.querySelector(selector);
      if (!root) return [];
      const cssPath = (element) => {
        const groups = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
          if (current.id) {
            groups.unshift(`#${CSS.escape(current.id)}`);
            const treeRoot = current.getRootNode?.();
            if (treeRoot instanceof ShadowRoot && treeRoot.host) { current = treeRoot.host; continue; }
            break;
          }
          const segments = [];
          while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
            let segment = current.tagName.toLowerCase();
            const classes = Array.from(current.classList).filter(Boolean).slice(0, 2);
            if (classes.length) segment += classes.map((name) => `.${CSS.escape(name)}`).join("");
            const siblings = current.parentElement
              ? Array.from(current.parentElement.children).filter((item) => item.tagName === current.tagName)
              : [];
            if (siblings.length > 1) segment += `:nth-of-type(${siblings.indexOf(current) + 1})`;
            segments.unshift(segment);
            if (segments.length >= 6 || !current.parentElement) break;
            current = current.parentElement;
          }
          groups.unshift(segments.join(" > "));
          const treeRoot = current?.getRootNode?.();
          if (treeRoot instanceof ShadowRoot && treeRoot.host) current = treeRoot.host;
          else break;
        }
        return groups.join(" >>> ");
      };
      const collectElements = (start) => {
        const result = [];
        const visit = (element) => {
          result.push(element);
          for (const child of element.children) visit(child);
          if (element.shadowRoot) for (const child of element.shadowRoot.children) visit(child);
        };
        visit(start);
        return result;
      };
      const durationMs = (value) => String(value || "").split(",").reduce((max, item) => {
        const token = item.trim();
        const numeric = Number.parseFloat(token);
        if (!Number.isFinite(numeric)) return max;
        return Math.max(max, token.endsWith("ms") ? numeric : numeric * 1000);
      }, 0);
      const entries = [];
      const elements = collectElements(root);
      for (const element of elements) {
        const style = getComputedStyle(element);
        if (style.animationName !== "none" && durationMs(style.animationDuration) > 0) {
          entries.push({
            sourceSelector: cssPath(element),
            source: "css-animation",
            name: style.animationName,
            properties: [],
            durationMs: durationMs(style.animationDuration),
            delayMs: durationMs(style.animationDelay),
            timingFunction: style.animationTimingFunction,
            iterationCount: style.animationIterationCount,
            direction: style.animationDirection,
            fillMode: style.animationFillMode,
            playState: style.animationPlayState,
            keyframes: [],
          });
        }
        if (style.transitionProperty !== "none" && durationMs(style.transitionDuration) > 0) {
          entries.push({
            sourceSelector: cssPath(element),
            source: "css-transition",
            name: null,
            properties: style.transitionProperty.split(",").map((item) => item.trim()),
            durationMs: durationMs(style.transitionDuration),
            delayMs: durationMs(style.transitionDelay),
            timingFunction: style.transitionTimingFunction,
            iterationCount: "1",
            direction: "normal",
            fillMode: "none",
            playState: "idle",
            keyframes: [],
          });
        }
      }
      const animations = typeof document.getAnimations === "function" ? document.getAnimations() : [];
      for (const animation of animations) {
        const effect = animation.effect;
        const target = effect && effect.target instanceof Element ? effect.target : null;
        let current = target;
        let insideRoot = false;
        while (current) {
          if (current === root) { insideRoot = true; break; }
          const treeRoot = current.getRootNode?.();
          current = current.parentElement || (treeRoot instanceof ShadowRoot ? treeRoot.host : null);
        }
        if (!target || !insideRoot) continue;
        const timing = effect.getTiming ? effect.getTiming() : {};
        const keyframes = effect.getKeyframes ? effect.getKeyframes().map((frame) => {
          const clean = {};
          for (const [key, value] of Object.entries(frame)) {
            if (["offset", "computedOffset", "easing", "composite"].includes(key) || typeof value === "string" || typeof value === "number") clean[key] = value;
          }
          return clean;
        }) : [];
        const properties = Array.from(new Set(keyframes.flatMap((frame) => Object.keys(frame)).filter((key) => !["offset", "computedOffset", "easing", "composite"].includes(key))));
        entries.push({
          sourceSelector: cssPath(target),
          source: "web-animation",
          name: animation.id || animation.animationName || null,
          properties,
          durationMs: Number(timing.duration) || 0,
          delayMs: Number(timing.delay) || 0,
          timingFunction: timing.easing || "linear",
          iterationCount: timing.iterations == null ? 1 : String(timing.iterations),
          direction: timing.direction || "normal",
          fillMode: timing.fill || "none",
          playState: animation.playState,
          keyframes,
        });
      }
      const webAnimationKeys = new Set(entries.filter((entry) => entry.source === "web-animation").map((entry) => `${entry.sourceSelector}|${entry.name || ""}`));
      const seen = new Set();
      return entries.filter((entry) => {
        if (entry.source === "css-animation" && webAnimationKeys.has(`${entry.sourceSelector}|${entry.name || ""}`)) return false;
        const key = JSON.stringify([entry.sourceSelector, entry.source, entry.name, entry.properties, entry.durationMs, entry.delayMs]);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).map((entry, index) => ({ id: `motion-${index + 1}`, ...entry }));
    }, rootSelector) : [];
    await page.addStyleTag({ content: `
      *, *::before, *::after {
        animation: none !important;
        transition: none !important;
        caret-color: transparent !important;
      }
      video, audio { visibility: hidden !important; }
    ` });
    await page.evaluate(async () => {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    if (args.waitMs) await page.waitForTimeout(args.waitMs);

    const rootHandle = await page.$(rootSelector);
    if (!rootHandle) throw new Error(`Root selector did not match: ${rootSelector}`);

    const extracted = await page.evaluate((selector) => {
      const root = document.querySelector(selector);
      if (!root) throw new Error(`Root selector did not match: ${selector}`);

      const clean = (value) => value == null ? null : String(value);
      const directText = (element) => Array.from(element.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent || "")
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
      const cssPath = (element) => {
        const groups = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
          if (current.id) {
            groups.unshift(`#${CSS.escape(current.id)}`);
            const treeRoot = current.getRootNode?.();
            if (treeRoot instanceof ShadowRoot && treeRoot.host) { current = treeRoot.host; continue; }
            break;
          }
          const segments = [];
          while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
            let segment = current.tagName.toLowerCase();
            const classes = Array.from(current.classList).filter(Boolean).slice(0, 2);
            if (classes.length) segment += classes.map((name) => `.${CSS.escape(name)}`).join("");
            const siblings = current.parentElement
              ? Array.from(current.parentElement.children).filter((item) => item.tagName === current.tagName)
              : [];
            if (siblings.length > 1) segment += `:nth-of-type(${siblings.indexOf(current) + 1})`;
            segments.unshift(segment);
            if (segments.length >= 6 || !current.parentElement) break;
            current = current.parentElement;
          }
          groups.unshift(segments.join(" > "));
          const treeRoot = current?.getRootNode?.();
          if (treeRoot instanceof ShadowRoot && treeRoot.host) current = treeRoot.host;
          else break;
        }
        return groups.join(" >>> ");
      };
      const elements = [];
      const visit = (element) => {
        elements.push(element);
        for (const child of element.children) visit(child);
        if (element.shadowRoot) for (const child of element.shadowRoot.children) visit(child);
      };
      visit(root);
      const idByElement = new Map();
      const usedIds = new Set();
      elements.forEach((element, index) => {
        const preferred = element.getAttribute("data-ios-node-id") || element.id || `node-${index + 1}`;
        let runtimeId = preferred.replace(/[^A-Za-z0-9._-]+/g, "-") || `node-${index + 1}`;
        let suffix = 2;
        while (usedIds.has(runtimeId)) runtimeId = `${preferred}-${suffix++}`;
        usedIds.add(runtimeId);
        idByElement.set(element, runtimeId);
      });
      const contentRuns = (element) => Array.from(element.childNodes).flatMap((child) => {
        if (child.nodeType === Node.TEXT_NODE) {
          const raw = child.textContent || "";
          const text = raw.replace(/\s+/g, " ");
          return text.trim() ? [{ kind: "text", text }] : [];
        }
        if (child.nodeType === Node.ELEMENT_NODE && idByElement.has(child)) {
          const text = (child.innerText || child.textContent || "").replace(/\s+/g, " ");
          return text.trim() ? [{ kind: "node", runtimeId: idByElement.get(child), text }] : [];
        }
        return [];
      });
      const styleObject = (style) => ({
        display: style.display,
        visibility: style.visibility,
        position: style.position,
        top: style.top,
        right: style.right,
        bottom: style.bottom,
        left: style.left,
        boxSizing: style.boxSizing,
        width: style.width,
        height: style.height,
        minWidth: style.minWidth,
        maxWidth: style.maxWidth,
        minHeight: style.minHeight,
        maxHeight: style.maxHeight,
        margin: [style.marginTop, style.marginRight, style.marginBottom, style.marginLeft],
        padding: [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft],
        gap: style.gap,
        rowGap: style.rowGap,
        columnGap: style.columnGap,
        flexDirection: style.flexDirection,
        flexWrap: style.flexWrap,
        flexGrow: style.flexGrow,
        flexShrink: style.flexShrink,
        flexBasis: style.flexBasis,
        justifyContent: style.justifyContent,
        alignItems: style.alignItems,
        alignSelf: style.alignSelf,
        gridTemplateColumns: style.gridTemplateColumns,
        gridTemplateRows: style.gridTemplateRows,
        color: style.color,
        backgroundColor: style.backgroundColor,
        backgroundImage: style.backgroundImage,
        backgroundPosition: style.backgroundPosition,
        backgroundSize: style.backgroundSize,
        backgroundRepeat: style.backgroundRepeat,
        opacity: style.opacity,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        fontStyle: style.fontStyle,
        lineHeight: style.lineHeight,
        letterSpacing: style.letterSpacing,
        textAlign: style.textAlign,
        textDecoration: style.textDecorationLine,
        textTransform: style.textTransform,
        whiteSpace: style.whiteSpace,
        wordBreak: style.wordBreak,
        overflowWrap: style.overflowWrap,
        textOverflow: style.textOverflow,
        webkitLineClamp: style.webkitLineClamp,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
        borderWidths: [style.borderTopWidth, style.borderRightWidth, style.borderBottomWidth, style.borderLeftWidth],
        borderColors: [style.borderTopColor, style.borderRightColor, style.borderBottomColor, style.borderLeftColor],
        borderStyles: [style.borderTopStyle, style.borderRightStyle, style.borderBottomStyle, style.borderLeftStyle],
        cornerRadii: [style.borderTopLeftRadius, style.borderTopRightRadius, style.borderBottomRightRadius, style.borderBottomLeftRadius],
        boxShadow: style.boxShadow,
        transform: style.transform,
        transformOrigin: style.transformOrigin,
        zIndex: style.zIndex,
        objectFit: style.objectFit,
        objectPosition: style.objectPosition,
        clipPath: style.clipPath,
        filter: style.filter,
        backdropFilter: style.backdropFilter || style.webkitBackdropFilter || "none",
        pointerEvents: style.pointerEvents,
      });
      const pseudoObject = (element, pseudo) => {
        const style = getComputedStyle(element, pseudo);
        if (!style || style.display === "none" || style.content === "none" || style.content === "normal") return null;
        return { content: style.content, style: styleObject(style) };
      };
      const textMetricsObject = (element, style) => {
        const ownText = directText(element);
        const textTag = /^(a|button|label|legend|li|p|span|strong|em|small|h[1-6]|td|th|textarea)$/i.test(element.tagName);
        if (!ownText && !textTag) return null;
        const renderedText = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
        if (!renderedText) return null;
        const range = document.createRange();
        range.selectNodeContents(element);
        const fragments = Array.from(range.getClientRects())
          .filter((rect) => rect.width > 0 && rect.height > 0)
          .map((rect) => ({ x: rect.x, y: rect.y, width: rect.width, height: rect.height, top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left }));
        const lines = [];
        for (const fragment of fragments.sort((a, b) => a.top - b.top || a.left - b.left)) {
          const line = lines.find((candidate) => Math.abs(candidate.top - fragment.top) <= 1.5);
          if (!line) {
            lines.push({ ...fragment });
            continue;
          }
          line.left = Math.min(line.left, fragment.left);
          line.top = Math.min(line.top, fragment.top);
          line.right = Math.max(line.right, fragment.right);
          line.bottom = Math.max(line.bottom, fragment.bottom);
          line.x = line.left;
          line.y = line.top;
          line.width = line.right - line.left;
          line.height = line.bottom - line.top;
        }
        let fontLoaded = null;
        try {
          fontLoaded = document.fonts ? document.fonts.check(`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`) : null;
        } catch (_) {
          fontLoaded = null;
        }
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d");
        let fontMetrics = null;
        if (context) {
          context.font = style.font || `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
          const measured = context.measureText("Hg中文");
          fontMetrics = {
            actualBoundingBoxAscent: measured.actualBoundingBoxAscent,
            actualBoundingBoxDescent: measured.actualBoundingBoxDescent,
            fontBoundingBoxAscent: measured.fontBoundingBoxAscent ?? null,
            fontBoundingBoxDescent: measured.fontBoundingBoxDescent ?? null,
          };
        }
        const ascent = fontMetrics?.actualBoundingBoxAscent || 0;
        return {
          renderedText,
          directText: ownText,
          lineCount: lines.length,
          lineRects: lines,
          fontLoaded,
          fontMetrics,
          firstBaselineY: lines.length ? lines[0].top + ascent : null,
          lastBaselineY: lines.length ? lines[lines.length - 1].top + ascent : null,
          clippedHorizontally: element.scrollWidth > element.clientWidth + 1,
          clippedVertically: element.scrollHeight > element.clientHeight + 1,
        };
      };
      const serializedSVG = (element) => {
        const clone = element.cloneNode(true);
        const originals = [element, ...element.querySelectorAll("*")];
        const clones = [clone, ...clone.querySelectorAll("*")];
        const properties = [
          ["fill", "fill"], ["stroke", "stroke"], ["stroke-width", "strokeWidth"],
          ["stroke-linecap", "strokeLinecap"], ["stroke-linejoin", "strokeLinejoin"],
          ["stroke-dasharray", "strokeDasharray"], ["stroke-dashoffset", "strokeDashoffset"],
          ["fill-opacity", "fillOpacity"], ["stroke-opacity", "strokeOpacity"], ["opacity", "opacity"],
        ];
        originals.forEach((original, index) => {
          const copy = clones[index];
          const computed = getComputedStyle(original);
          for (const [attribute, property] of properties) {
            const value = computed[property];
            if (value && value !== "normal") copy.setAttribute(attribute, value);
          }
          copy.removeAttribute("class");
          copy.removeAttribute("style");
        });
        return clone.outerHTML;
      };
      const elementNodes = elements.map((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const effectivelyVisible = typeof element.checkVisibility === "function"
          ? element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
          : style.display !== "none" && style.visibility !== "hidden" && Number.parseFloat(style.opacity || "1") > 0;
        const treeRoot = element.getRootNode();
        const parent = element === root ? null : (element.parentElement || (treeRoot instanceof ShadowRoot ? treeRoot.host : null));
        const tag = element.tagName.toLowerCase();
        const attributes = {};
        for (const name of [
          "role", "aria-label", "aria-hidden", "aria-checked", "aria-selected", "aria-expanded",
          "aria-valuemin", "aria-valuemax", "aria-valuenow", "aria-controls", "aria-current", "aria-haspopup",
          "href", "target", "type", "name", "placeholder", "value", "disabled", "checked",
          "selected", "required", "readonly", "multiple", "min", "max", "step", "maxlength",
          "pattern", "autocomplete", "inputmode", "contenteditable", "open", "tabindex", "autofocus",
          "for", "accept", "capture", "onclick", "action",
          "data-ios-node-id", "data-ios-action", "data-ios-target", "data-ios-component", "data-ios-project-component",
          "data-ios-presentation-style", "data-ios-detents", "data-ios-container",
          "data-ios-app-root", "data-ios-screen", "data-ios-screen-title", "data-ios-screen-initial", "data-ios-module",
          "data-ios-ignore", "data-ios-shell", "data-ios-system-chrome", "data-ios-safe-area",
          "data-ios-owner", "data-ios-backdrop-dismiss", "data-ios-interactive-dismiss",
          "data-ios-navigation-style", "data-ios-title-mode", "data-ios-scroll-edge",
          "data-ios-toolbar-placement", "data-ios-back-button",
          "data-ios-tab-id", "data-ios-tab-title", "data-ios-icon", "data-ios-selected-icon",
          "data-ios-badge", "data-ios-tab-role", "data-ios-reselect", "data-ios-tab-visibility",
          "data-ios-state", "data-ios-state-kind", "data-ios-visible-when",
          "data-ios-visual-state", "data-ios-required-state", "data-ios-scroll-root",
          "data-ios-animation", "data-ios-duration-ms", "data-ios-delay-ms", "data-ios-easing",
          "data-ios-repeat", "data-ios-reduced-motion",
        ]) {
          if (element.hasAttribute(name)) attributes[name] = element.getAttribute(name);
        }
        const asset = tag === "img" ? (element.currentSrc || element.getAttribute("src"))
          : tag === "video" || tag === "source" ? element.getAttribute("src")
          : tag === "svg" ? "inline-svg"
          : style.backgroundImage && style.backgroundImage !== "none" ? style.backgroundImage
          : null;
        return {
          runtimeId: idByElement.get(element),
          parentRuntimeId: parent && idByElement.has(parent) ? idByElement.get(parent) : null,
          selector: cssPath(element),
          tag,
          domId: element.id || null,
          classNames: Array.from(element.classList),
          encapsulation: {
            customElement: element.tagName.includes("-"),
            shadowHost: Boolean(element.shadowRoot),
            insideShadowRoot: treeRoot instanceof ShadowRoot,
            shadowMode: element.shadowRoot?.mode || null,
          },
          attributes,
          properties: {
            value: "value" in element ? clean(element.value) : null,
            checked: "checked" in element ? Boolean(element.checked) : null,
            selected: "selected" in element ? Boolean(element.selected) : null,
            disabled: "disabled" in element ? Boolean(element.disabled) : null,
            readOnly: "readOnly" in element ? Boolean(element.readOnly) : null,
            required: "required" in element ? Boolean(element.required) : null,
            multiple: "multiple" in element ? Boolean(element.multiple) : null,
            open: "open" in element ? Boolean(element.open) : null,
            focused: document.activeElement === element,
          },
          text: directText(element),
          contentRuns: contentRuns(element),
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height, top: rect.top, right: rect.right, bottom: rect.bottom, left: rect.left },
          scroll: { scrollWidth: element.scrollWidth, scrollHeight: element.scrollHeight, clientWidth: element.clientWidth, clientHeight: element.clientHeight },
          visible: effectivelyVisible && rect.width > 0 && rect.height > 0,
          style: styleObject(style),
          pseudo: { before: pseudoObject(element, "::before"), after: pseudoObject(element, "::after") },
          asset: clean(asset),
          assetDetails: tag === "svg"
            ? { kind: "inline-svg", markup: serializedSVG(element) }
            : tag === "img"
              ? { kind: "image", url: clean(element.currentSrc || element.getAttribute("src")), alt: element.getAttribute("alt") }
              : tag === "video" || tag === "source"
                ? { kind: "media", url: clean(element.currentSrc || element.getAttribute("src")), poster: element.getAttribute("poster") }
                : style.backgroundImage && style.backgroundImage !== "none"
                  ? { kind: "css-background", value: style.backgroundImage, position: style.backgroundPosition, size: style.backgroundSize, repeat: style.backgroundRepeat }
                  : null,
          textMetrics: textMetricsObject(element, style),
        };
      });
      const px = (value) => {
        const numeric = Number.parseFloat(value);
        return Number.isFinite(numeric) && String(value).trim().endsWith("px") ? numeric : null;
      };
      const pseudoNodes = [];
      for (const node of elementNodes) {
        for (const kind of ["before", "after"]) {
          const pseudo = node.pseudo[kind];
          if (!pseudo) continue;
          const style = pseudo.style;
          const width = px(style.width);
          const height = px(style.height);
          const left = px(style.left);
          const top = px(style.top);
          const estimatedRect = {
            x: node.rect.x + (left || 0),
            y: node.rect.y + (top || 0),
            width: width == null ? node.rect.width : width,
            height: height == null ? node.rect.height : height,
          };
          estimatedRect.top = estimatedRect.y;
          estimatedRect.left = estimatedRect.x;
          estimatedRect.right = estimatedRect.x + estimatedRect.width;
          estimatedRect.bottom = estimatedRect.y + estimatedRect.height;
          const content = String(pseudo.content || "").replace(/^['"]|['"]$/g, "");
          pseudoNodes.push({
            runtimeId: `${node.runtimeId}--${kind}`,
            parentRuntimeId: node.runtimeId,
            selector: `${node.selector}::${kind}`,
            tag: `::${kind}`,
            domId: null,
            classNames: [],
            attributes: { "aria-hidden": "true" },
            properties: {},
            text: content,
            rect: estimatedRect,
            rectEstimated: true,
            scroll: { scrollWidth: estimatedRect.width, scrollHeight: estimatedRect.height, clientWidth: estimatedRect.width, clientHeight: estimatedRect.height },
            visible: estimatedRect.width > 0 && estimatedRect.height > 0,
            style,
            pseudo: { before: null, after: null },
            asset: style.backgroundImage && style.backgroundImage !== "none" ? style.backgroundImage : null,
            synthetic: { kind: "pseudo-element", pseudo: kind, ownerRuntimeId: node.runtimeId },
          });
        }
      }
      const nodes = [...elementNodes, ...pseudoNodes];
      const rawCandidates = nodes.map((node) => {
        const { width, height } = node.rect;
        if (width <= 0 || height <= 0) return null;
        let score = 0;
        const reasons = [];
        const ratio = height / width;
        const nameBlob = `${node.domId || ""} ${node.classNames.join(" ")}`;
        const frameName = /(phone|iphone|device|artboard|mockup)/i.test(nameBlob);
        const appName = /(^|[^a-z])(app|screen|mobile)([^a-z]|$)/i.test(nameBlob);
        const mobileWidth = width >= 280 && width <= 500;
        const phoneAspect = ratio >= 1.5 && ratio <= 2.4;
        const hasShadow = node.style.boxShadow !== "none";
        const radius = parseFloat(node.style.cornerRadii[0]);
        const largeRadius = Number.isFinite(radius) && radius >= 20;
        const largeOuterCanvas = document.documentElement.clientWidth > width * 1.7;
        const visualFrame = hasShadow && largeRadius;
        if (!(mobileWidth && phoneAspect && (frameName || appName || visualFrame))) return null;
        if (mobileWidth) { score += 2; reasons.push("mobile-width"); }
        if (phoneAspect) { score += 2; reasons.push("phone-aspect"); }
        if (frameName) { score += 4; reasons.push("device-frame-name"); }
        if (appName) { score += 3; reasons.push("app-root-name"); }
        if (hasShadow) { score += 1; reasons.push("shadow"); }
        if (largeRadius) { score += 1; reasons.push("large-radius"); }
        if (largeOuterCanvas) { score += 1; reasons.push("large-outer-canvas"); }
        return {
          runtimeId: node.runtimeId,
          selector: node.selector,
          rect: node.rect,
          score,
          reasons,
          kind: frameName || visualFrame ? "device-frame" : "app-root",
        };
      }).filter(Boolean).sort((a, b) => b.score - a.score);
      const nodeById = new Map(nodes.map((node) => [node.runtimeId, node]));
      const candidateById = new Map(rawCandidates.map((candidate) => [candidate.runtimeId, candidate]));
      const candidates = rawCandidates.map((candidate) => {
        let parentId = nodeById.get(candidate.runtimeId)?.parentRuntimeId || null;
        let containedByRuntimeId = null;
        while (parentId) {
          if (candidateById.has(parentId)) {
            containedByRuntimeId = parentId;
            break;
          }
          parentId = nodeById.get(parentId)?.parentRuntimeId || null;
        }
        return { ...candidate, containedByRuntimeId, recommendedRootRuntimeId: candidate.runtimeId, isPrimary: !containedByRuntimeId };
      });
      for (const candidate of candidates) {
        if (candidate.kind !== "device-frame") continue;
        const nestedRoots = candidates.filter((nested) =>
          nested.kind === "app-root"
          && nested.containedByRuntimeId === candidate.runtimeId
          && nested.rect.width >= candidate.rect.width * 0.9
          && nested.rect.height >= candidate.rect.height * 0.8
        );
        if (nestedRoots.length) candidate.recommendedRootRuntimeId = nestedRoots.sort((a, b) => b.score - a.score)[0].runtimeId;
      }
      const interactions = nodes.filter((node) => {
        const role = node.attributes.role;
        return ["a", "button", "input", "select", "textarea", "form", "summary"].includes(node.tag)
          || ["button", "link", "checkbox", "switch", "radio", "slider", "tab", "menuitem"].includes(role)
          || node.attributes.contenteditable === "true" || node.attributes.onclick
          || node.attributes["data-ios-action"];
      }).map((node) => ({
        sourceRuntimeId: node.runtimeId,
        sourceRole: node.attributes.role || null,
        sourceType: node.attributes.type || null,
        sourceTag: node.tag,
        trigger: node.tag === "form" ? "submit" : "tap",
        href: node.attributes.href || null,
        target: node.attributes.target || null,
        action: node.attributes.action || null,
        inlineHandler: node.attributes.onclick || null,
        iosAction: node.attributes["data-ios-action"] || null,
        iosTarget: node.attributes["data-ios-target"] || null,
        iosComponent: node.attributes["data-ios-component"] || null,
        iosPresentationStyle: node.attributes["data-ios-presentation-style"] || null,
        iosDetents: node.attributes["data-ios-detents"] || null,
        iosContainer: node.attributes["data-ios-container"] || null,
        iosOwner: node.attributes["data-ios-owner"] || null,
        iosState: node.attributes["data-ios-state"] || null,
        iosStateKind: node.attributes["data-ios-state-kind"] || null,
        iosVisibleWhen: node.attributes["data-ios-visible-when"] || null,
        iosVisualState: node.attributes["data-ios-visual-state"] || null,
        iosAnimation: node.attributes["data-ios-animation"] || null,
        iosDurationMs: node.attributes["data-ios-duration-ms"] || null,
        iosDelayMs: node.attributes["data-ios-delay-ms"] || null,
        iosEasing: node.attributes["data-ios-easing"] || null,
      }));
      const viewportMeta = document.querySelector('meta[name="viewport"]');
      return {
        document: {
          title: document.title,
          url: location.href,
          language: document.documentElement.lang || null,
          direction: getComputedStyle(document.documentElement).direction || document.documentElement.dir || "ltr",
          viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
          documentSize: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
          metaViewport: viewportMeta ? viewportMeta.getAttribute("content") : null,
          rootSelector: selector,
          loadedFonts: document.fonts ? Array.from(document.fonts).map((face) => ({
            family: face.family,
            style: face.style,
            weight: face.weight,
            stretch: face.stretch,
            status: face.status,
          })) : [],
        },
        nodes,
        interactions,
        phoneCandidates: candidates,
      };
    }, rootSelector);

    const parentIds = new Set(extracted.nodes.map((node) => node.parentRuntimeId).filter(Boolean));
    for (const node of extracted.nodes) {
      if (node.encapsulation?.customElement && !node.encapsulation.shadowHost && !parentIds.has(node.runtimeId) && node.visible) {
        warnings.push(`Opaque custom element candidate requires visual review: ${node.selector}`);
      }
      if (["iframe", "embed", "object"].includes(node.tag) && node.visible) {
        warnings.push(`Embedded content requires origin-specific review: ${node.selector}`);
      }
    }

    const screenshotPath = args.screenshot ? path.resolve(args.screenshot) : null;
    if (screenshotPath) {
      fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
      if (args.selector) await rootHandle.screenshot({ path: screenshotPath, animations: "disabled" });
      else await page.screenshot({ path: screenshotPath, fullPage: true, animations: "disabled" });
    }

    const output = {
      schemaVersion: "render-tree-1.2",
      source: args.html ? { kind: "html-file", entry: path.resolve(args.html) } : { kind: "url", entry: args.url },
      capturedAt: new Date().toISOString(),
      screenshot: screenshotPath,
      warnings: Array.from(new Set(warnings)),
      motions,
      ...extracted,
    };
    const outPath = path.resolve(args.out);
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify({ out: outPath, screenshot: screenshotPath, nodes: output.nodes.length, motions: output.motions.length, phoneCandidates: output.phoneCandidates.length, warnings: output.warnings }, null, 2)}\n`);
    await context.close();
  } finally {
    if (browser) await browser.close();
    if (server) await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
