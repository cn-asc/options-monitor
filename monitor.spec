# PyInstaller spec for Options Monitor (double-click app for teammates).
# Build: pyinstaller monitor.spec

import sys
import os

block_cipher = None

# Bundle certifi CA cert so HTTPS (Polygon API, etc.) works in the frozen app
import certifi
_certifi_dir = os.path.dirname(certifi.where())

# Local modules and template must be included.
# _MEIPASS will contain daily_email.html when frozen.
a = Analysis(
    ['monitor_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('daily_email.html', '.'),
        ('assets/BANNER.png', 'assets'),
        ('assets/LOGO.svg', 'assets'),
        (_certifi_dir, 'certifi'),
    ],
    hiddenimports=[
        'certifi',
        'flask',
        'jinja2',
        'dotenv',
        'options_dashboard_v2',
        'app_v2',
        'insight_engine_light',
        'market_trends_watcher',
        'feedparser',
        'openai',
        'pandas',
        'numpy',
        'requests',
        'yfinance',
        'httpx',
        'zoneinfo',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Options Monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console so users see progress; set False for no terminal window
)
