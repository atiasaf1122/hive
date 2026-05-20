# HIVE desktop

Tauri 2 + React 18 + Vite + TailwindCSS shell for HIVE. The Python FastAPI
backend is launched as a child process when the app starts.

## Requirements

- Node 20+
- Rust + Cargo (`https://www.rust-lang.org/tools/install`)
- The HIVE Python `.venv` already created at the repo root (run
  `uv pip install -e ".[dev]"` from `~/hive`)
- Platform-specific WebView2/WebKit deps Tauri requires (see
  [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/))

## Develop

```bash
cd desktop
npm install
npm run tauri:dev
```

### Icons

The full icon set (PNGs, `.ico`, `.icns`) is committed under
`src-tauri/icons/` so the build works on a fresh clone. If you ever
need to change the logo, edit `scripts/generate_icons.py` and re-run:

```bash
python scripts/generate_icons.py
```

`build.rs` fails fast on Windows if `src-tauri/icons/icon.ico` is
missing — that's the file the Rust resource compiler embeds into the
.exe.

`tauri:dev`:

1. Compiles the Rust shell.
2. Starts Vite on `:1420` for the React UI with HMR.
3. Opens a desktop window.
4. The Rust side spawns `python -m uvicorn backend.main:app --port 8765`
   using the project's `.venv`. The React splash polls `/health` and
   transitions to the main UI once the backend responds.

## Build

```bash
npm run tauri:build
```

Outputs `.deb` / `.AppImage` on Linux, `.dmg` on macOS, `.msi` on Windows
under `src-tauri/target/release/bundle/`.

## Layout

```
desktop/
├── src/                    React (Vite) frontend
│   ├── App.tsx
│   ├── main.tsx
│   ├── components/
│   ├── pages/
│   ├── stores/
│   └── lib/
├── src-tauri/
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── capabilities/
│   └── src/
│       ├── main.rs
│       └── lib.rs          spawns + tracks the Python sidecar
└── package.json
```

## Phase status

| Phase | Status | Description |
|-------|--------|-------------|
| A | ✅ | Scaffold + sidecar + theme + sidebar |
| B | ⬜ | Projects dashboard + project view + composer |
| C | ⬜ | Automations / Skills / Plugins / Usage / Settings |
| D | ⬜ | Polish + bundled installers |
