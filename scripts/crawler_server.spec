# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['Y:\\Stage25\\M.Espion\\crawler_suite_patch\\\\run_server.py'],
    pathex=['Y:\\Stage25\\M.Espion\\crawler_suite_patch\\\\src'],
    binaries=[],
    datas=[('Y:\\Stage25\\M.Espion\\crawler_suite_patch\\\\frontend', 'frontend'), ('Y:\\Stage25\\M.Espion\\crawler_suite_patch\\\\var', 'var')],
    hiddenimports=['bs4', 'lxml', 'pandas', 'numpy', 'sklearn'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'tensorflow', 'onnxruntime'],
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
    name='crawler_server',
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
