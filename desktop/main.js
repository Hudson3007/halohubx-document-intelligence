const { app, BrowserWindow } = require("electron");
const http = require("http");
const path = require("path");
const fs = require("fs");
const url = require("url");

const API_BASE = process.env.HALOHUBX_API || "http://127.0.0.1:8000";
const API_PREFIXES = [
  "/upload",
  "/auth",
  "/billing",
  "/usage",
  "/audit",
  "/hitl",
  "/documents",
  "/status",
  "/retrieve",
];

let mainWindow;
let server;
const SERVER_PORT = parseInt(process.env.HALOHUBX_PORT || "17800", 10);

function resolveDist() {
  if (app.isPackaged) {
    const p = path.join(process.resourcesPath, "app-dist");
    if (fs.existsSync(p)) return p;
  }
  const candidates = [
    path.join(__dirname, "..", "frontend", "dist"),
    path.join(__dirname, "..", "..", "..", "frontend", "dist"),
    path.join(app.getAppPath(), "..", "frontend", "dist"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "index.html"))) return c;
  }
  return candidates[0];
}
const DIST = resolveDist();

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
};

function isApi(p) {
  return API_PREFIXES.some((prefix) => p === prefix || p.startsWith(prefix + "/"));
}

function proxy(req, res) {
  const target = new URL(req.url, API_BASE);
  const opts = {
    hostname: target.hostname,
    port: target.port,
    path: target.pathname + target.search,
    method: req.method,
    headers: Object.assign({}, req.headers, { host: target.host }),
  };
  const upstream = http.request(opts, (upRes) => {
    res.writeHead(upRes.statusCode, upRes.headers);
    upRes.pipe(res);
  });
  upstream.on("error", () => {
    const body = JSON.stringify({
      error: "backend_unreachable",
      detail:
        "API server not running. Start it with:  uvicorn app.main:app --host 127.0.0.1 --port 8000",
    });
    res.writeHead(502, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    });
    res.end(body);
  });
  req.pipe(upstream);
}

function serve(req, res) {
  const parsed = url.parse(req.url);
  let filePath = path.join(DIST, parsed.pathname === "/" ? "index.html" : parsed.pathname);
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    filePath = path.join(DIST, "index.html");
  }
  try {
    const data = fs.readFileSync(filePath);
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(data);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}

function createServer() {
  server = http.createServer((req, res) => {
    const pathname = url.parse(req.url).pathname;
    if (isApi(pathname)) {
      proxy(req, res);
    } else if (pathname === "/" && (!req.headers.accept || !req.headers.accept.includes("text/html"))) {
      proxy(req, res);
    } else {
      serve(req, res);
    }
  });
  server.listen(SERVER_PORT, "127.0.0.1", () => {
    console.log(`[HaloHubX Desktop] serving console on http://127.0.0.1:${SERVER_PORT}`);
    console.log(`[HaloHubX Desktop] proxying API to ${API_BASE}`);
  });
  server.on("error", (err) => {
    if (err.code === "EADDRINUSE") {
      console.error(`[HaloHubX Desktop] port ${SERVER_PORT} in use — set HALOHUBX_PORT`);
    }
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: "HaloHubX Document Intelligence",
    icon: path.join(__dirname, "..", "frontend", "dist", "favicon.ico"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.loadURL(`http://127.0.0.1:${SERVER_PORT}`);
  mainWindow.setMenu(null);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  createServer();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (server) server.close();
  app.quit();
});
