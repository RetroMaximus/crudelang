# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the ``crude`` CLI (stage eleven of the plan).

Two things have to be true for a bundled build to run a project on a machine
that has no development environment:

* the ``crude`` package is collected whole, so every phase module, binding and
  renderer the loader reaches at run time is inside the archive;
* the DLLs in ``libs/`` - ``libpd.dll`` and the mingw runtime it links against
  (``libc++.dll``, ``libunwind.dll``, ``libwinpthread-1.dll``, ``libstdc++-6.dll``,
  ``libpd.lib`` for anyone who wants to link against it) - travel with the
  executable as *binaries* so ``ctypes`` (and Windows' loader) can find them.

:func:`crude.pd.lib.dll_bootstrap` looks for them under ``sys._MEIPASS/libs``
before anywhere else, so a bundled build reads exactly the layout written here.
"""

import glob
import os

from PyInstaller.utils.hooks import collect_all

# ``libs/`` sits beside the spec file (repo root); every DLL and the import
# library in it is copied into the bundle under the same ``libs/`` prefix so
# ``dll_bootstrap`` finds it via ``sys._MEIPASS/libs/libpd.dll`` unchanged.
HERE = os.path.abspath(os.path.dirname(SPEC)) if 'SPEC' in globals() else os.getcwd()
LIBS_DIR = os.path.join(HERE, "libs")

datas = []
binaries = []
hiddenimports = ["win32security", "win32gui", "win32timezone"]

# the whole package: submodules the loader imports by name at run time
for name in ("crude",):
    try:
        tmp_ret = collect_all(name)
    except Exception:  # pragma: no cover - the package is always there
        continue
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

# ``libs/*.dll`` (and ``libpd.lib`` for anyone who wants to link against the
# bundle) land beside the frozen exe under the same ``libs/`` prefix
if os.path.isdir(LIBS_DIR):
    for pattern in ("*.dll", "*.lib", "*.so", "*.dylib"):
        for found in glob.glob(os.path.join(LIBS_DIR, pattern)):
            binaries.append((found, "libs"))


a = Analysis(
    ["C:/pythonprojects/crudelang/crude/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="crude",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
