# Building a HIVE installer

This is the runbook for producing the packaged installers
(`HIVE-0.9.0-x64.msi` on Windows, `.dmg` on macOS, `.AppImage` on Linux).
You need to run it on the target OS — PyInstaller doesn't cross-compile.

## Prerequisites

| | Windows | macOS | Linux |
|---|---------|-------|-------|
| Python | 3.11 in WSL **or** native | 3.11 | 3.11 |
| Node | 20.x | 20.x | 20.x |
| Rust | stable (rustup) | stable | stable |
| Build tools | Visual Studio 2022 Build Tools (C++) | Xcode CLT | `build-essential`, `libwebkit2gtk-4.1-dev`, etc. — see <https://v2.tauri.app/start/prerequisites/> |

## Step 1 — freeze the Python backend

From the repo root:

```bash
uv pip install pyinstaller            # one-time
uv run pyinstaller packaging/hive-backend.spec --clean --noconfirm
```

This produces `dist/hive-backend/hive-backend(.exe)` plus a `_internal/`
folder of bundled Python deps.

### Smoke-test the freeze before packaging

```bash
./dist/hive-backend/hive-backend &
curl -s http://127.0.0.1:8765/health   # should print {"status":"ok",...}
pkill hive-backend
```

If `/health` doesn't respond, fix the freeze before continuing — the
Tauri install will inherit any breakage.

## Step 2 — drop the freeze into Tauri's sidecar slot

Tauri's `bundle.externalBin` looks for files named with the **rustc host
triple**. Determine it once:

```bash
rustc -vV | grep host        # e.g. host: x86_64-pc-windows-msvc
```

Then rename + copy:

```bash
# Windows (in WSL or PowerShell — adjust paths for the shell)
cp -r dist/hive-backend  desktop/src-tauri/binaries/hive-backend-x86_64-pc-windows-msvc/
mv    desktop/src-tauri/binaries/hive-backend-x86_64-pc-windows-msvc/hive-backend.exe \
      desktop/src-tauri/binaries/hive-backend-x86_64-pc-windows-msvc.exe

# macOS arm64
mv dist/hive-backend desktop/src-tauri/binaries/hive-backend-aarch64-apple-darwin

# Linux x86_64
mv dist/hive-backend desktop/src-tauri/binaries/hive-backend-x86_64-unknown-linux-gnu
```

(`bundle.externalBin` references the triple-suffixed name; Tauri's
sidecar resolver finds the right binary per platform.)

## Step 3 — build the installer

```bash
cd desktop
npm install                  # if you haven't already
npm run tauri:build          # applies src-tauri/tauri.conf.prod.json
```

The `tauri:build` npm script passes `--config src-tauri/tauri.conf.prod.json`,
which merges the `bundle.externalBin: ["binaries/hive-backend"]` field on
top of the base config. That keeps the dev workflow (`npm run tauri:dev`)
working on machines that don't have the frozen sidecar binary yet — the
external-bin requirement only applies at production-build time.

If you need to override more fields for the production build (e.g.
custom signing config), add them to `tauri.conf.prod.json` next to
the bundle block.

Output ends up in `desktop/src-tauri/target/release/bundle/`:

- Windows: `msi/HIVE_0.9.0_x64_en-US.msi`
- macOS:   `dmg/HIVE_0.9.0_aarch64.dmg`
- Linux:   `appimage/HIVE_0.9.0_amd64.AppImage` and `deb/...`

## Step 4 — verify on a clean machine

The single most important test:

> Install the .msi on a fresh Windows 11 VM that has **no Python**, **no
> WSL**, **no `claude` CLI**.

1. Run the .msi installer.
2. Launch HIVE from the Start menu.
3. First-run onboarding opens. Step 2 ("Connect Claude") detects the
   missing `claude` CLI and offers the install link.
4. After `claude setup-token` in the helper terminal, the splash
   resolves to the dashboard.
5. Create a session — agents actually run (preflight catches missing
   git identity from inside the bundled backend).

If any of those steps fail, the package isn't ready to ship.

## Step 5 — sign

- **Windows**: sign with `signtool` using a code-signing cert. Without
  signing, Defender SmartScreen will warn users. Tauri config supports
  `windows.signCommand` for this.
- **macOS**: notarise via `notarytool`. Without notarisation, Gatekeeper
  blocks the .dmg on first open.
- **Linux**: AppImage signing is optional; we don't ship a key yet.

## Data dir migration

In dev: `~/.hive/`. In the packaged build the entrypoint switches to
the OS-conventional location:

| OS | Path |
|---|------|
| Windows | `%APPDATA%\HIVE\` |
| macOS   | `~/Library/Application Support/HIVE/` |
| Linux   | `~/.local/share/HIVE/` |

`backend.persistence.db.HIVE_DIR` already reads `$HIVE_DIR`, so the
switch is a one-line change in `packaging/entrypoint.py` (already done).
