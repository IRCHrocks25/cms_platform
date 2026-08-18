#!/usr/bin/env node

import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";

function argument(name, fallback = "") {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

const base = argument("base", "http://127.0.0.1:8000").replace(/\/$/, "");
const path = argument("path", "/dashboard/");
const output = argument("out");
const inspectSelector = argument("inspect");
const clickSelector = argument("click");
const scrollSelector = argument("scroll-to");
const menuAuditLabel = argument("menu-audit");
const editorAudit = process.argv.includes("--editor-audit");
const tabAuditCount = Number(argument("tab-audit", "0"));
const width = Number(argument("width", "1280"));
const height = Number(argument("height", "900"));
const username = process.env.CMS_CAPTURE_USERNAME || "admin";
const password = process.env.CMS_CAPTURE_PASSWORD;

if (!output || !password || !Number.isFinite(width) || !Number.isFinite(height)) {
  console.error("Usage: CMS_CAPTURE_PASSWORD=… node scripts/capture-ui.mjs --out FILE --path /dashboard/ --width 1280 [--height 900]");
  process.exit(2);
}

const profile = await mkdtemp(join(tmpdir(), "cms-ui-capture-"));
const chromium = spawn(
  process.env.CHROMIUM_BIN || "/usr/bin/chromium",
  [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--hide-scrollbars",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: "ignore" },
);

const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function debuggingPort() {
  const portFile = join(profile, "DevToolsActivePort");
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const [port] = (await readFile(portFile, "utf8")).split("\n");
      if (port) return port;
    } catch {
      // Chromium writes the file once its debugging endpoint is ready.
    }
    await pause(100);
  }
  throw new Error("Chromium debugging endpoint did not start");
}

let socket;
try {
  const port = await debuggingPort();
  const target = await fetch(`http://127.0.0.1:${port}/json/new`, { method: "PUT" }).then((response) => response.json());
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let sequence = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });

  function command(method, params = {}) {
    const id = ++sequence;
    socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }

  await Promise.all([
    command("Page.enable"),
    command("Runtime.enable"),
    command("Network.enable"),
    command("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: width <= 560,
      screenWidth: width,
      screenHeight: height,
    }),
  ]);

  await command("Page.navigate", { url: `${base}/login/` });
  await pause(700);
  const loginResult = await command("Runtime.evaluate", {
    expression: `(() => {
      const username = document.querySelector('[name="username"]');
      const password = document.querySelector('[name="password"]');
      const form = username?.form;
      if (!username || !password || !form) return 'missing-login-form';
      username.value = ${JSON.stringify(username)};
      password.value = ${JSON.stringify(password)};
      form.requestSubmit();
      return 'submitted';
    })()`,
    returnByValue: true,
  });
  if (loginResult.result.value !== "submitted") throw new Error("Login form was not found");
  await pause(900);

  await command("Page.navigate", { url: `${base}${path}` });
  await pause(1100);
  if (scrollSelector) {
    await command("Runtime.evaluate", {
      expression: `document.querySelector(${JSON.stringify(scrollSelector)})?.scrollIntoView({ block: 'start' })`,
    });
    await pause(300);
  }
  if (clickSelector) {
    const clickResult = await command("Runtime.evaluate", {
      expression: `(() => {
        const element = document.querySelector(${JSON.stringify(clickSelector)});
        if (!element) return false;
        element.click();
        return true;
      })()`,
      returnByValue: true,
    });
    if (!clickResult.result.value) throw new Error(`Click target was not found: ${clickSelector}`);
    await pause(300);
  }
  if (editorAudit) {
    const seeded = await command("Runtime.evaluate", {
      expression: `(() => {
        const textarea = document.querySelector('textarea[data-code-editor]');
        const view = textarea && window.CMSCodeEditor?.instances.get(textarea);
        if (!textarea || !view) return false;
        const section = '<section data-section="feature"><h2 data-edit="feature.title">A realistic heading for editor height measurement</h2><p data-edit="feature.copy">Representative body copy for a large imported page.</p></section>\\n';
        const source = '<!doctype html>\\n<html><body>\\n' + section.repeat(6000) + '</body></html>';
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: source } });
        return { bytes: source.length, lines: 6003 };
      })()`,
      returnByValue: true,
    });
    if (!seeded.result.value) throw new Error("CodeMirror editor was not found");
    await pause(1000);
    const measure = async () => {
      const result = await command("Runtime.evaluate", {
        expression: `(() => {
          const editor = document.querySelector('.cms-code-editor .cm-editor');
          const scroller = document.querySelector('.cms-code-editor .cm-scroller');
          const saveRow = document.querySelector('.source-form-actions');
          const rect = (element) => element ? {
            top: Math.round(element.getBoundingClientRect().top),
            bottom: Math.round(element.getBoundingClientRect().bottom),
            height: Math.round(element.getBoundingClientRect().height),
          } : null;
          return {
            viewport: { width: innerWidth, height: innerHeight },
            document: { scrollHeight: document.documentElement.scrollHeight, scrollY: Math.round(scrollY) },
            editor: rect(editor),
            scroller: {
              ...rect(scroller),
              clientHeight: scroller?.clientHeight,
              scrollHeight: scroller?.scrollHeight,
              overflowY: scroller ? getComputedStyle(scroller).overflowY : null,
            },
            saveRow: rect(saveRow),
          };
        })()`,
        returnByValue: true,
      });
      return result.result.value;
    };
    const initial = await measure();
    await command("Runtime.evaluate", {
      expression: "window.scrollTo(0, Math.max(0, document.documentElement.scrollHeight / 2))",
    });
    await pause(300);
    const midpoint = await measure();
    console.log(JSON.stringify({ editorAudit: { seeded: seeded.result.value, initial, midpoint } }, null, 2));
  }
  if (menuAuditLabel) {
    async function press(key, code, windowsVirtualKeyCode) {
      await command("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode });
      await command("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode });
    }
    let reachedTrigger = false;
    for (let index = 0; index < 80; index += 1) {
      await press("Tab", "Tab", 9);
      const label = await command("Runtime.evaluate", {
        expression: "document.activeElement?.getAttribute('aria-label') || ''",
        returnByValue: true,
      });
      if (label.result.value.startsWith(menuAuditLabel)) {
        reachedTrigger = true;
        break;
      }
    }
    if (!reachedTrigger) throw new Error(`Menu trigger was not reachable by Tab: ${menuAuditLabel}`);
    await press("ArrowDown", "ArrowDown", 40);
    const opened = await command("Runtime.evaluate", {
      expression: `({
        expanded: document.activeElement?.closest('[data-menu]')?.querySelector('[data-menu-trigger]')?.getAttribute('aria-expanded'),
        firstItem: document.activeElement?.textContent?.trim(),
      })`,
      returnByValue: true,
    });
    await press("ArrowDown", "ArrowDown", 40);
    const moved = await command("Runtime.evaluate", {
      expression: "document.activeElement?.textContent?.trim()",
      returnByValue: true,
    });
    await press("Escape", "Escape", 27);
    const closed = await command("Runtime.evaluate", {
      expression: `({
        label: document.activeElement?.getAttribute('aria-label'),
        expanded: document.activeElement?.getAttribute('aria-expanded'),
      })`,
      returnByValue: true,
    });
    const result = { opened: opened.result.value, movedTo: moved.result.value, closed: closed.result.value };
    console.log(JSON.stringify({ menuAudit: result }, null, 2));
    if (result.opened.expanded !== "true" || result.closed.expanded !== "false" || !result.closed.label?.startsWith(menuAuditLabel)) {
      throw new Error("Row menu keyboard contract failed");
    }
  }
  if (inspectSelector) {
    const inspection = await command("Runtime.evaluate", {
      expression: `(() => {
        const element = document.querySelector(${JSON.stringify(inspectSelector)});
        if (!element) return null;
        const style = getComputedStyle(element);
        return {
          className: element.className,
          display: style.display,
          background: style.backgroundColor,
          flex: style.flex,
          height: style.height,
          alignContent: style.alignContent,
          gridTemplateRows: style.gridTemplateRows,
        };
      })()`,
      returnByValue: true,
    });
    console.log(JSON.stringify(inspection.result.value));
  }
  if (Number.isInteger(tabAuditCount) && tabAuditCount > 0) {
    const stops = [];
    for (let index = 0; index < tabAuditCount; index += 1) {
      await command("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      await command("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      const focused = await command("Runtime.evaluate", {
        expression: `(() => {
          const el = document.activeElement;
          if (!el) return null;
          return {
            tag: el.tagName,
            id: el.id || null,
            label: el.getAttribute('aria-label') || el.getAttribute('title') || null,
            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
            outline: getComputedStyle(el).outlineStyle,
            boxShadow: getComputedStyle(el).boxShadow,
          };
        })()`,
        returnByValue: true,
      });
      stops.push(focused.result.value);
    }
    console.log(JSON.stringify({ tabStops: stops }, null, 2));
  }
  const screenshot = await command("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false,
  });

  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, Buffer.from(screenshot.data, "base64"));
  console.log(`Captured ${basename(output)} at ${width}×${height}`);
} finally {
  if (socket?.readyState === WebSocket.OPEN) socket.close();
  if (chromium.exitCode === null) {
    const exited = new Promise((resolve) => chromium.once("exit", resolve));
    chromium.kill("SIGTERM");
    await exited;
  }
  await rm(profile, { recursive: true, force: true });
}
