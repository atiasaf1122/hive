//! Build script.
//!
//! On Windows we need a .ico embedded as a Windows resource so the .exe shows
//! the HIVE icon in Explorer / taskbar. tauri-build does this automatically
//! from `bundle.icon` in `tauri.conf.json`, but only if the file actually
//! exists on disk. We fail loud if it doesn't — easier to read than the
//! cascading "package.metadata does not exist" error tauri-build emits when
//! it can't find the icon.

fn main() {
    #[cfg(target_os = "windows")]
    {
        use std::path::Path;
        let ico = Path::new("icons").join("icon.ico");
        if !ico.exists() {
            panic!(
                "icons/icon.ico is missing. Generate icons with:\n    \
                python desktop/scripts/generate_icons.py\n  \
                then re-run `npm run tauri:dev`."
            );
        }
    }
    tauri_build::build()
}
