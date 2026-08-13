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
