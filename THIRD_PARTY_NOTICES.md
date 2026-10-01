# Third-party components

LensTracker uses unmodified, dynamically loaded components distributed through PyPI:

- Python — Python Software Foundation License; https://www.python.org/ ; https://github.com/python/cpython
- PySide6 / Shiboken6 / Qt — https://www.qt.io/qt-for-python ; https://code.qt.io/pyside/pyside-setup.git/ ; https://code.qt.io/qt/ ; https://doc.qt.io/qt-6/licensing.html
- Qt WebEngine / Chromium — https://doc.qt.io/qt-6/qtwebengine-licensing.html ; https://chromium.googlesource.com/chromium/src/
- Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, Click — Pallets projects, BSD licenses; https://palletsprojects.com/
- Blinker — MIT License; https://github.com/pallets-eco/blinker
- Pillow — HPND License; https://python-pillow.github.io/ ; https://github.com/python-pillow/Pillow
- PyInstaller (build tool and bootloader) — https://pyinstaller.org/ ; https://github.com/pyinstaller/pyinstaller

Installed distribution metadata and supplied license files are copied into `_internal/licenses`. Python's license is included with its bundled runtime. Qt libraries remain separate DLLs in the onedir package and may be replaced with compatible builds. No user photo, private database, or personal price record is included in this distribution.

Exact bundled versions: Python 3.14.4, PySide6/Qt/Shiboken6 6.11.2, Flask 3.1.2, Pillow 12.2.0. Build requirements are pinned in `requirements-build.txt` and its included requirement files. Upstream component licenses and notices continue to apply.
