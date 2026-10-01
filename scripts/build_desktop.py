"""Reproducible Windows onedir distribution and portable ZIP."""
from importlib import metadata
from pathlib import Path
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import zipfile
import urllib.request

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_NAME = 'Garden_Tools_LensTracker.exe'


def release_version():
    return runpy.run_path(str(ROOT / 'lenstracker' / 'version.py'))['VERSION']


def prepare_assets():
    source = ROOT / 'icon.png'
    if not source.is_file():
        raise FileNotFoundError(f'The supplied application icon is required: {source}')
    # Encode the supplied artwork as a multi-resolution Windows icon, unchanged.
    with Image.open(source) as image:
        icon = image.convert('RGBA')
        icon.thumbnail((256,256), Image.Resampling.LANCZOS)
    icon.save(ROOT / 'static' / 'app.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    shutil.copy2(source, ROOT / 'static' / 'app-icon.png')
    licenses = ROOT / 'build' / 'licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    qt_licenses = licenses / 'Qt'
    qt_licenses.mkdir(exist_ok=True)
    for name in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'GPL-2.0-only.txt', 'Qt-GPL-exception-1.0.txt'):
        target = qt_licenses / name
        if not target.exists():
            url = f'https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/{name}'
            with urllib.request.urlopen(url, timeout=30) as response:
                target.write_bytes(response.read())
    for name in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6',
                 'Flask', 'Werkzeug', 'Jinja2', 'MarkupSafe', 'itsdangerous', 'blinker', 'click', 'Pillow'):
        distribution = metadata.distribution(name)
        package_dir = licenses / name
        package_dir.mkdir(exist_ok=True)
        (package_dir / 'METADATA.txt').write_text(distribution.read_text('METADATA') or name, encoding='utf-8')
        for item in distribution.files or []:
            if any(part.lower() in ('licenses', 'license', 'license.txt', 'license.rst', 'copying') for part in item.parts):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    shutil.copy2(source, package_dir / source.name)


def is_user_data(path):
    """Keep portable archives free of application state, including old layouts."""
    name = path.name.casefold()
    return ('data' in (part.casefold() for part in path.parts)
            or name == 'window.ini'
            or name.startswith(('lenstracker.sqlite3', 'library.sqlite3', 'desktop.log'))
            or name.endswith('.log'))


def archive_distribution(folder=None, archive=None):
    """Archive application files; default calls refresh both release and latest ZIPs.

    An explicit archive path writes only that file, allowing main() to stage both
    archives before publishing them together with the executable.
    """
    folder = folder or ROOT / 'dist' / 'LensTracker'
    for name in ('DESKTOP.md', 'THIRD_PARTY_NOTICES.md', 'VALIDATION.md', 'CHANGELOG.md'):
        shutil.copy2(ROOT / name, folder / name)
    shutil.copy2(ROOT / 'README.md', folder / '_internal' / 'README.md')
    shutil.copytree(ROOT / 'build' / 'licenses', folder / '_internal' / 'licenses', dirs_exist_ok=True)
    # Separate replaceable DLLs are intentionally retained in the portable folder.
    refresh_latest = archive is None
    archive = archive or folder.parent / f'LensTracker-{release_version()}-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and not is_user_data(path.relative_to(folder)):
                output.write(path, path.relative_to(folder.parent))
    if refresh_latest:
        shutil.copy2(archive, folder.parent / 'LensTracker-Windows-x64.zip')
    return archive


def workspace_path(path):
    """Validate targets before moving files or cleaning a temporary build tree."""
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError(f'Build path must stay inside the workspace: {path}')
    return resolved


def build_launcher(staged):
    """Compile a console-free Windows entry point beside the project files."""
    framework = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET'
    compiler = next((framework / arch / 'v4.0.30319' / 'csc.exe'
                     for arch in ('Framework64', 'Framework')
                     if (framework / arch / 'v4.0.30319' / 'csc.exe').is_file()), None)
    if compiler is None:
        raise FileNotFoundError('The Windows .NET Framework C# compiler is required.')
    launcher = staged / LAUNCHER_NAME
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/optimize+',
                    '/reference:System.Windows.Forms.dll',
                    f'/win32icon:{ROOT / "static" / "app.ico"}',
                    f'/out:{launcher}', str(ROOT / 'scripts' / 'launcher.cs')],
                   cwd=ROOT, check=True)
    return launcher


def publish_distribution(folder, archive, *additional_archives, launcher=None):
    """Replace generated files while leaving a live installation's data in place."""
    destination = workspace_path(ROOT / 'dist' / 'LensTracker')
    destination.mkdir(parents=True, exist_ok=True)
    previous = workspace_path(folder.parent / 'previous')
    previous.mkdir()
    replacements = [(source, destination / source.name) for source in sorted(folder.iterdir())
                    if not is_user_data(Path(source.name))]
    archives = (archive, *additional_archives)
    replacements.extend((item, destination.parent / item.name) for item in archives)
    if launcher is not None:
        replacements.append((launcher, workspace_path(ROOT / LAUNCHER_NAME)))
    changes = []
    try:
        for source, target in replacements:
            workspace_path(source)
            workspace_path(target)
            backup = previous / target.name
            existed = target.exists()
            if existed:
                target.rename(backup)
            changes.append((source, target, backup, existed))
            source.rename(target)
    except BaseException:
        for source, target, backup, existed in reversed(changes):
            if target.exists():
                target.rename(source)
            if existed:
                backup.rename(target)
        raise
    print(f'Executable: {destination / "LensTracker.exe"}')
    if launcher is not None:
        print(f'Project launcher: {ROOT / LAUNCHER_NAME}')
    for item in archives:
        print(f'Portable ZIP: {destination.parent / item.name}')


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows distribution on Windows.')
    version = release_version()
    prepare_assets()
    build = workspace_path(ROOT / 'build')
    build.mkdir(parents=True, exist_ok=True)
    # PyInstaller's --noconfirm may delete its output directory. Never aim it at
    # the installed application, whose data directory contains the user's cache.
    with tempfile.TemporaryDirectory(prefix='desktop-dist-', dir=build) as temporary:
        staged = workspace_path(Path(temporary))
        launcher = build_launcher(staged)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                        '--distpath', str(staged), str(ROOT / 'LensTracker.spec')],
                       cwd=ROOT, check=True)
        folder = staged / 'LensTracker'
        archive = archive_distribution(folder, staged / f'LensTracker-{version}-Windows-x64.zip')
        latest = staged / 'LensTracker-Windows-x64.zip'
        # A byte-identical alias avoids recompressing the bundled Qt runtime.
        shutil.copy2(archive, latest)
        publish_distribution(folder, archive, latest, launcher=launcher)


if __name__ == '__main__':
    main()
