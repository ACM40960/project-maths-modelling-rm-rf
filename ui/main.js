// ui/main.js
const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");
const fse = require("fs-extra");
const os = require("os");
const { marked } = require("marked");
const HTMLtoDOCX = require("html-to-docx");

function createWindow() {
  const win = new BrowserWindow({
    width: 1000,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile("index.html");
}
app.whenReady().then(createWindow);

// Prefer the venv python; fallback to system python.
function guessPython() {
  const candidates = [
    process.env.PYTHON,
    path.resolve(__dirname, "..", "project_view", "Scripts", "python.exe"), // your venv
    path.resolve(__dirname, "..", ".venv", "Scripts", "python.exe"), // common alt
    "python",
  ].filter(Boolean);
  for (const p of candidates) {
    try {
      if (p === "python") return p;
      if (fs.existsSync(p)) return p;
    } catch {}
  }
  return "python";
}
const PYTHON = guessPython();

// Always run from app/ so .env + outputs (docs/, debug/, docs_index/) are discovered
const APP_DIR = path.resolve(__dirname, "..", "app");

/* ---------------- helpers ---------------- */

function dedupeTopH1(md) {
  const lines = md.replace(/^\uFEFF/, "").split(/\r?\n/); // strip BOM
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i++;
  if (i >= lines.length) return md;

  const a = lines[i].match(/^#\s+(.+)/);
  if (!a) return md;
  const first = (a[1] || "").trim().toLowerCase();

  let j = i + 1;
  while (j < lines.length && lines[j].trim() === "") j++;
  if (j >= lines.length) return md;

  const b = lines[j].match(/^#\s+(.+)/);
  if (b && (b[1] || "").trim().toLowerCase() === first) {
    lines.splice(j, 1);
    if (j < lines.length && lines[j].trim() === "") lines.splice(j, 1);
  }
  return lines.join("\n");
}

function resolveMmdcBin() {
  const exe = process.platform === "win32" ? "mmdc.cmd" : "mmdc";
  const candidates = [
    path.join(__dirname, "node_modules", ".bin", exe),
    path.join(process.cwd(), "node_modules", ".bin", exe),
  ];
  for (const p of candidates) if (fs.existsSync(p)) return p;
  return exe; // rely on PATH (global install)
}

function runShellCommand(cmd) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, { shell: true });
    let stderr = "";
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("error", (err) => reject(err));
    child.on("close", (code) =>
      code === 0
        ? resolve()
        : reject(new Error(stderr || `command exited ${code}`))
    );
  });
}

// Render Mermaid to PNG (Word-safe) and return data URL
async function mermaidToDataUrl(code) {
  const bin = resolveMmdcBin();
  const tmpDir = await fse.mkdtemp(path.join(os.tmpdir(), "mmd-"));
  const inFile = path.join(tmpDir, "in.mmd");
  const outFile = path.join(tmpDir, "out.png");
  await fse.writeFile(inFile, code, "utf8");

  // white background + width for readability
  const cmd = `"${bin}" -i "${inFile}" -o "${outFile}" -e png --backgroundColor white --width 1400`;
  await runShellCommand(cmd);

  const png = await fse.readFile(outFile);
  const base64 = png.toString("base64");
  await fse.rm(tmpDir, { recursive: true, force: true }).catch(() => {});
  return `data:image/png;base64,${base64}`;
}

async function replaceMermaidBlocks(md) {
  const re = /```mermaid\s*([\s\S]*?)```/g;
  let m,
    last = 0,
    out = [];
  while ((m = re.exec(md)) !== null) {
    out.push(md.slice(last, m.index));
    const dataUrl = await mermaidToDataUrl(m[1]);
    out.push(`\n\n<img src="${dataUrl}" alt="Mermaid diagram"/>\n\n`);
    last = re.lastIndex;
  }
  out.push(md.slice(last));
  return out.join("");
}

// Extract first H1 from markdown
function extractH1(md) {
  const m = md.match(/^\s*#\s+(.+?)\s*$/m);
  return m ? m[1].trim() : null;
}

// Desired order:
// 0 Objective & Scope → 1 Installation & Setup → 2 Technologies Used → 3 System Architecture → 4 API Key → 999 others
function sectionRank(titleOrPath) {
  const s = (titleOrPath || "").toLowerCase();
  if (s.includes("objective") || s.includes("scope") || s.includes("overview"))
    return 0;
  if (
    s.includes("installation") ||
    s.includes("setup") ||
    s.includes("getting started")
  )
    return 1;
  if (
    s.includes("technolog") ||
    s.includes("tech stack") ||
    s.includes("dependencies")
  )
    return 2;
  if (s.includes("system architecture") || s.includes("architecture")) return 3;
  if (
    s.includes("api key") ||
    s.includes("api keys") ||
    s.includes("api access")
  )
    return 4;
  return 999;
}

// Cover title from repo URL
function repoNameFromUrl(repoUrl) {
  try {
    const u = new URL(repoUrl);
    const parts = u.pathname.split("/").filter(Boolean); // ["owner","repo"]
    let repo = parts[1] || parts[parts.length - 1] || "Repository";
    repo = repo.replace(/\.git$/i, "");
    return repo;
  } catch {
    const m = (repoUrl || "").match(/\/([^\/]+?)(?:\.git)?\/?$/);
    return m && m[1] ? m[1] : "Repository";
  }
}

/* ---------------- IPC: Generate ---------------- */

ipcMain.handle("generate-doc", async (evt, { repoUrl }) => {
  // 0) log chosen interpreter
  evt.sender.send("log", `Using PYTHON: ${PYTHON}\n`);

  // 1) run Python pipeline: python app/main.py --repo <url> (UTF-8 forced)
  const script = path.join(APP_DIR, "main.py");
  const child = spawn(PYTHON, [script, "--repo", repoUrl], {
    cwd: APP_DIR,
    shell: false,
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
    },
  });

  child.stdout.on("data", (d) => evt.sender.send("log", d.toString()));
  child.stderr.on("data", (d) => evt.sender.send("log", d.toString()));

  const code = await new Promise((r) => child.on("close", r));
  if (code !== 0) throw new Error("Pipeline failed (see logs).");

  // 2) read, clean, and ORDER app/docs/*.md
  const docsDir = path.join(APP_DIR, "docs");
  if (!fs.existsSync(docsDir)) throw new Error("No docs/ directory produced.");
  const files = fs.readdirSync(docsDir).filter((n) => n.endsWith(".md"));
  if (!files.length) throw new Error("No Markdown files produced in docs/.");

  const entries = [];
  for (const name of files) {
    let md = fs.readFileSync(path.join(docsDir, name), "utf-8");
    md = dedupeTopH1(md);
    md = await replaceMermaidBlocks(md);

    const h1 = extractH1(md) || name.replace(/_/g, " ").replace(/\.md$/i, "");
    const rank = sectionRank(h1);
    entries.push({ name, md, rank, h1 });
  }

  // Enforce desired order, stable tie-break on filename
  entries.sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name));

  // Merge in the new order
  const mergedMd = entries.map((e) => `\n\n${e.md}\n`).join("");

  // 3) COVER PAGE: centered H1 with repo name + page break
  const repoName = repoNameFromUrl(repoUrl);
  const coverHtml = `
    <div class="cover">
      <h1>/${repoName}</h1>
    </div>
    <div class="pagebreak"></div>
  `;

  // 4) Markdown → HTML → DOCX
  const contentHtml = marked.parse(mergedMd);
  const docHtml = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { font-family: Arial, sans-serif; font-size: 11pt; }
  h1,h2,h3 { margin: 0.6em 0 0.3em; }
  .cover { text-align: center; margin-top: 260pt; } /* ~3.6in from top */
  .cover h1 { font-size: 32pt; margin: 0; }
  .pagebreak { page-break-after: always; }
  code, pre { font-family: Consolas, monospace; font-size: 10pt; }
  pre { white-space: pre-wrap; word-wrap: break-word; border: 1px solid #ddd; padding: 8px; }
  table { border-collapse: collapse; }
  th, td { border: 1px solid #ccc; padding: 6px 8px; }
  img { max-width: 100%; }
</style></head><body>
  ${coverHtml}
  ${contentHtml}
</body></html>`;

  const buf = await HTMLtoDOCX(docHtml);

  // 5) Save As
  const { filePath } = await dialog.showSaveDialog({
    title: "Save documentation",
    defaultPath: path.join(APP_DIR, "Technical_Documentation.docx"),
    filters: [{ name: "Word Document", extensions: ["docx"] }],
  });
  if (!filePath) return { saved: false };

  await fse.writeFile(filePath, buf);
  return { saved: true, path: filePath };
});
