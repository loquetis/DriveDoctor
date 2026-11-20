# PyInstaller spec: one-file, windowed DriveDoctor.exe with the web UI bundled.
# Build:  pyinstaller --noconfirm --clean DriveDoctor.spec
a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    datas=[('web', 'web'), ('bundled', 'bundled')],
    hiddenimports=['server', 'drives', 'storage', 'appserver'],
    excludes=['tkinter', 'unittest', 'pydoc', 'test', 'numpy'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='DriveDoctor',
    console=False,
    icon='drivedoctor.ico',
    upx=False,
    version='version_info.txt',
)
