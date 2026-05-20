# PyInstaller spec for the HIVE backend sidecar.
#
# Run on the target OS (cross-builds aren't supported by PyInstaller):
#
#     uv run pyinstaller packaging/hive-backend.spec --clean --noconfirm
#
# Output: dist/hive-backend/hive-backend(.exe)  — copy into Tauri's
# src-tauri/binaries/ folder, renamed with the platform triple suffix so
# Tauri's sidecar resolution picks the right one:
#
#     dist/hive-backend/hive-backend.exe → src-tauri/binaries/hive-backend-x86_64-pc-windows-msvc.exe
#     dist/hive-backend/hive-backend     → src-tauri/binaries/hive-backend-x86_64-apple-darwin
#     ...
#
# The Tauri side declares this binary under bundle.externalBin so it's
# bundled into the .msi/.dmg/.AppImage and the spawn path in lib.rs
# launches it (Phase 9D wires that).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

hiddenimports = []
hiddenimports += collect_submodules("backend")
hiddenimports += collect_submodules("langgraph")
hiddenimports += collect_submodules("aiosqlite")
hiddenimports += collect_submodules("aiogram")
hiddenimports += collect_submodules("apscheduler")
hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
]

datas = []
datas += collect_data_files("sentence_transformers")
datas += collect_data_files("certifi")

a = Analysis(
    ["entrypoint.py"],
    pathex=["../"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL.ImageTk"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="hive-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep stderr/stdout visible — Tauri inherits it
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="hive-backend",
)
