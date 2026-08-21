# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec для Medhar.exe."""

import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(ROOT, "templates"), "templates"),
    (os.path.join(ROOT, "static"), "static"),
    (os.path.join(ROOT, "data", "combat_diagnosis_patterns.json"), "data"),
    (os.path.join(ROOT, "data", "lpz_list.json"), "data"),
    (os.path.join(ROOT, "data", "likar_specializations.json"), "data"),
    (os.path.join(ROOT, "data", "service_signatories.json"), "data"),
    (os.path.join(ROOT, "data", "vlk_signatories.json"), "data"),
    (os.path.join(ROOT, "data", "all_tcc_ukraine.json"), "data"),
]
_team_vault = os.path.join(ROOT, "data", "team.vault")
if os.path.isfile(_team_vault):
    datas.append((_team_vault, "data"))
env_example = os.path.join(ROOT, ".env.example")
if os.path.isfile(env_example):
    datas.append((env_example, "."))

# pymorphy3 словники та інші data-файли
for pkg in ("pymorphy3", "pymorphy3_dicts_uk", "docxtpl"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

hiddenimports = [
    "webview",
    "webview.platforms.edgechromium",
    "flask",
    "jinja2",
    "werkzeug",
    "pandas",
    "numpy",
    "openpyxl",
    "docx",
    "docxtpl",
    "pymorphy3",
    "pymorphy3_dicts_uk",
    "dotenv",
    "dropbox",
    "libsql_client",
    "aiohttp",
    "google.genai",
    "google.genai.types",
    "utils.circumstances_parser",
    "utils.ukrainian_pib_genitive",
    "utils.patient_cards_db",
    "utils.payments_unpaid",
    "utils.dropbox_sync",
    "utils.gemini_extract",
    "utils.db_backend",
    "utils.db_cache",
    "utils.team_vault",
    "utils.sync_schema",
    "utils.journal_sync",
    "utils.team_tasks_db",
    "utils.discord_api",
    "utils.tcc_directory",
    "desktop.paths",
    "desktop.run_desktop",
]

binaries = []
tmp_ret = collect_all("webview")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    [os.path.join(ROOT, "desktop", "run_desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "notebook"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Medhar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # без UPX — менше хибних спрацювань антивірусу
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "desktop", "medhar.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Medhar",
)
