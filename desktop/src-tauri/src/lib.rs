//! HIVE Tauri shell.
//!
//! Backend lifecycle policy:
//!
//!   1. **Pre-flight probe.** On startup we open a TCP connection to
//!      `127.0.0.1:8765`. If something answers, the React side will reach
//!      that backend over the same loopback — we don't spawn a competing
//!      one. This is the dev workflow on Windows: the FastAPI sidecar runs
//!      inside WSL2 (`hive start`), WSL2 forwards loopback transparently
//!      between host and guest, the Tauri shell on Windows finds it.
//!
//!   2. **Spawn fallback.** If nothing's listening, we look for the project's
//!      `.venv` and run `python -m uvicorn backend.main:app --port 8765`.
//!      This is what the packaged .msi will do once Phase 9D bundles a
//!      Python interpreter — the same code path, different `python`.
//!
//!   3. **Clean shutdown.** We only kill backends we spawned ourselves.
//!      The existing WSL backend keeps running after the window closes.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, RunEvent, WindowEvent,
};

/// Wrapper around the backend child so the manager state stays Send + Sync.
pub struct BackendProcess(pub Mutex<Option<Child>>);

/// What `spawn_backend` decided to do — surfaced to logs for clarity.
enum BackendOutcome {
    Existing,
    Spawned,
}

#[tauri::command]
fn backend_url() -> String {
    "http://127.0.0.1:8765".to_string()
}

#[tauri::command]
fn backend_alive(state: tauri::State<BackendProcess>) -> bool {
    let mut guard = state.0.lock().unwrap();
    match guard.as_mut() {
        Some(child) => matches!(child.try_wait(), Ok(None)),
        None => true, // No child means "we didn't spawn one"; an external one is running.
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            // ── Build a minimal tray icon — the frontend will update its
            // tooltip/menu live via the `update_tray_*` IPC commands once
            // we know whether any background work is running.
            let open_item = MenuItem::with_id(app, "open", "Open HIVE", true, None::<&str>)?;
            let new_window_item =
                MenuItem::with_id(app, "new-window", "New window\tCtrl+Shift+N", true, None::<&str>)?;
            let pause_item =
                MenuItem::with_id(app, "pause-all", "Pause all automations", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let tray_menu = Menu::with_items(
                app,
                &[&open_item, &new_window_item, &pause_item, &quit_item],
            )?;

            let icon = app
                .default_window_icon()
                .cloned()
                .unwrap_or_else(|| Image::new_owned(Vec::new(), 0, 0));

            TrayIconBuilder::with_id("hive-tray")
                .tooltip("HIVE")
                .icon(icon)
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { .. } = event {
                        if let Some(window) = tray.app_handle().get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .on_menu_event(|app, event| {
                    let id = event.id().as_ref();
                    if id == "open" {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    } else if id == "new-window" {
                        // Same payload as the Ctrl+Shift+N shortcut — build
                        // a fresh window via Tauri's webview builder. We
                        // dispatch to the existing command so the logic
                        // lives in one place.
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let _ = open_new_window(app).await;
                        });
                    } else if id == "pause-all" {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("hive://tray-pause-all", ());
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    } else if id == "quit" {
                        app.exit(0);
                    }
                })
                .build(app)?;

            match ensure_backend(app.handle()) {
                Ok(BackendOutcome::Existing) => {
                    eprintln!("[hive] backend already responding on 127.0.0.1:8765 — skipping spawn");
                }
                Ok(BackendOutcome::Spawned) => {
                    eprintln!("[hive] spawned local backend");
                }
                Err(err) => {
                    eprintln!("[hive] no backend available: {err}");
                    eprintln!("[hive]   ↳ if you develop in WSL, run `hive start` inside WSL first");
                    eprintln!("[hive]   ↳ if you expect a bundled backend, this build is missing it");
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Defer to the React side: emit an event with a closing token,
                // prevent default close, and wait for the UI to either confirm
                // or cancel. The frontend listens for "hive://close-requested".
                api.prevent_close();
                let _ = window.emit("hive://close-requested", ());
            }
        })
        .invoke_handler(tauri::generate_handler![
            backend_url,
            backend_alive,
            confirm_close,
            update_tray_status,
            open_new_window
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<BackendProcess>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                        eprintln!("[hive] backend stopped");
                    }
                    // No child to kill ⇒ we connected to an external backend; leave it running.
                }
            }
        });
}

/// Called from the React side once the close dialog resolves.
///   confirm = true  → actually close the window
///   confirm = false → no-op (user cancelled)
#[tauri::command]
async fn confirm_close(window: tauri::Window, confirm: bool) -> Result<(), String> {
    if confirm {
        window.close().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Open a new window pointing at the same frontend. Each window gets a
/// unique label so Tauri tracks them independently; localStorage is
/// shared across windows of the same app (that's WebView default
/// behaviour), so saved tabs + settings stay in sync.
///
/// The React side binds this to Ctrl+Shift+N via `useGlobalShortcuts`.
#[tauri::command]
async fn open_new_window(app: tauri::AppHandle) -> Result<String, String> {
    use std::time::{SystemTime, UNIX_EPOCH};
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let label = format!("hive-{stamp}");

    let dev_url = "http://localhost:1420/".to_string();
    let url = tauri::WebviewUrl::App(std::path::PathBuf::from("/"));

    // Use the same URL pattern as the main window so the React app boots
    // identically. In dev that resolves to the Vite server; in production
    // it's the bundled index.html.
    let _ = dev_url; // kept for clarity — Tauri picks dev vs prod itself
    let _builder = tauri::WebviewWindowBuilder::new(&app, &label, url)
        .title("HIVE")
        .inner_size(1400.0, 900.0)
        .min_inner_size(1100.0, 700.0)
        .decorations(false)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(label)
}

/// Update the tray tooltip with the live count of running automations.
/// Called from the React polling loop in `useTrayHeartbeat`.
///
/// Phase-10 fix: track the last-applied visibility so repeated
/// `set_visible(false)` calls don't trigger Windows' "Error removing
/// system tray icon" message. The frontend already de-dupes its own
/// invocations, but defence-in-depth lives here too.
#[tauri::command]
fn update_tray_status(app: tauri::AppHandle, running: u32, tooltip: String) -> Result<(), String> {
    use std::sync::Mutex;

    // Process-local cache of the last applied (visible, tooltip) pair.
    static LAST: Mutex<Option<(bool, String)>> = Mutex::new(None);

    if let Some(tray) = app.tray_by_id("hive-tray") {
        let want_visible = running > 0;
        let mut guard = LAST.lock().map_err(|e| e.to_string())?;
        let needs_change = match &*guard {
            Some((prev_visible, prev_tooltip)) => {
                *prev_visible != want_visible || *prev_tooltip != tooltip
            }
            None => true,
        };
        if needs_change {
            let _ = tray.set_tooltip(Some(&tooltip));
            let _ = tray.set_visible(want_visible);
            *guard = Some((want_visible, tooltip));
        }
    }
    Ok(())
}

/// Decide whether to use an existing backend or spawn a new one.
fn ensure_backend(app: &tauri::AppHandle) -> Result<BackendOutcome, String> {
    // (1) Probe the port before doing anything destructive. On the WSL+Windows
    // dev workflow this answers immediately and we're done.
    if probe_backend(Duration::from_millis(500)) {
        return Ok(BackendOutcome::Existing);
    }

    // (2) Nothing's listening — try to spawn one.
    spawn_backend(app).map(|_| BackendOutcome::Spawned)
}

/// Poll 127.0.0.1:8765 with a short TCP connect, total budget ~`budget`.
/// Returns true as soon as the port answers, false if it never does.
fn probe_backend(budget: Duration) -> bool {
    use std::net::{SocketAddr, TcpStream};
    let addr: SocketAddr = "127.0.0.1:8765".parse().expect("static addr is valid");
    let deadline = Instant::now() + budget;
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(120)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}

fn spawn_backend(app: &tauri::AppHandle) -> Result<(), String> {
    // Production: prefer the frozen PyInstaller sidecar (`hive-backend(.exe)`)
    // that the .msi/.dmg/.AppImage installs alongside the main binary.
    if let Some(child) = spawn_sidecar_binary(app)? {
        if let Some(state) = app.try_state::<BackendProcess>() {
            *state.0.lock().unwrap() = Some(child);
        }
        return Ok(());
    }

    // Dev: fall back to running uvicorn from the repo .venv.
    let project_root = locate_project_root().ok_or_else(|| {
        "could not locate project root containing backend/main.py (no backend to spawn — \
         start one in WSL with `hive start`, or install one alongside this app)"
            .to_string()
    })?;

    let python = locate_python(&project_root).ok_or_else(|| {
        format!(
            "no Python interpreter found (looked for {}'s venv, then `python3`/`python` on PATH)",
            project_root.display()
        )
    })?;

    eprintln!(
        "[hive] starting backend (dev): {} -m uvicorn backend.main:app --port 8765 (cwd={})",
        python.display(),
        project_root.display()
    );

    let child = Command::new(&python)
        .args([
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--log-level",
            "info",
        ])
        .current_dir(&project_root)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("spawn failed: {e}"))?;

    if let Some(state) = app.try_state::<BackendProcess>() {
        *state.0.lock().unwrap() = Some(child);
    }
    Ok(())
}

/// Look for the packaged sidecar binary next to the app's resource dir.
/// Tauri's bundler drops `externalBin` files into the same folder as the
/// main executable. Returns Ok(None) if the binary isn't there — that's
/// the dev case and we fall through to the uvicorn path.
fn spawn_sidecar_binary(app: &tauri::AppHandle) -> Result<Option<Child>, String> {
    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let exe_name = if cfg!(target_os = "windows") {
        "hive-backend.exe"
    } else {
        "hive-backend"
    };
    let candidate = resource_dir.join(exe_name);
    if !candidate.exists() {
        return Ok(None);
    }
    eprintln!("[hive] starting bundled backend: {}", candidate.display());
    let child = Command::new(&candidate)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;
    Ok(Some(child))
}

/// Walk upward from the current dir until we find a folder containing
/// `backend/main.py` — that's the HIVE workspace root. Returns None if we
/// never find one (e.g. running on Windows from a folder copied without the
/// `backend/` tree — which is exactly the WSL+Windows dev case).
fn locate_project_root() -> Option<PathBuf> {
    let mut current = std::env::current_dir().ok()?;
    for _ in 0..6 {
        if current.join("backend").join("main.py").exists() {
            return Some(current);
        }
        if !current.pop() {
            break;
        }
    }
    None
}

fn locate_python(project_root: &Path) -> Option<PathBuf> {
    // Prefer the project's venv so we get the installed dependencies.
    #[cfg(target_os = "windows")]
    let venv_python = project_root.join(".venv").join("Scripts").join("python.exe");
    #[cfg(not(target_os = "windows"))]
    let venv_python = project_root.join(".venv").join("bin").join("python");

    if venv_python.exists() {
        return Some(venv_python);
    }

    // Fall back to whatever `python3` is on PATH.
    let candidates: &[&str] = &["python3", "python"];
    for name in candidates {
        if let Ok(out) = Command::new(name).arg("--version").output() {
            if out.status.success() {
                return Some(PathBuf::from(name));
            }
        }
    }
    None
}
