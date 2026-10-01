from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from scripts import build_desktop


SPEC = Path(build_desktop.ROOT) / 'LensTracker.spec'


@pytest.fixture
def build_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(build_desktop, 'ROOT', tmp_path)
    monkeypatch.setattr(build_desktop.sys, 'platform', 'win32')
    monkeypatch.setattr(build_desktop, 'prepare_assets', lambda: None)
    def fake_launcher(staged):
        launcher = staged / build_desktop.LAUNCHER_NAME
        launcher.write_bytes(b'new launcher')
        return launcher
    monkeypatch.setattr(build_desktop, 'build_launcher', fake_launcher)
    (tmp_path / build_desktop.LAUNCHER_NAME).write_bytes(b'old launcher')
    for name in ('DESKTOP.md', 'THIRD_PARTY_NOTICES.md', 'VALIDATION.md', 'README.md', 'CHANGELOG.md'):
        (tmp_path / name).write_text(name, encoding='utf-8')
    (tmp_path / 'lenstracker').mkdir()
    (tmp_path / 'lenstracker' / 'version.py').write_text("VERSION = '3.2.1'\n", encoding='utf-8')
    licenses = tmp_path / 'build' / 'licenses'
    licenses.mkdir(parents=True)
    (licenses / 'license.txt').write_text('license', encoding='utf-8')
    folder = tmp_path / 'dist' / 'LensTracker'
    (folder / '_internal').mkdir(parents=True)
    (folder / 'LensTracker.exe').write_bytes(b'old executable')
    (folder / '_internal' / 'old.dll').write_bytes(b'old library')
    (folder / 'data').mkdir()
    for name in ('lenstracker.sqlite3', 'lenstracker.sqlite3-wal', 'window.ini', 'desktop.log'):
        (folder / 'data' / name).write_bytes(b'private ' + name.encode())
    (tmp_path / 'dist' / 'LensTracker-Windows-x64.zip').write_bytes(b'old archive')
    (tmp_path / 'dist' / 'LensTracker-3.2.1-Windows-x64.zip').write_bytes(b'previous build of this version')
    (tmp_path / 'dist' / 'LensTracker-1.0.0-Windows-x64.zip').write_bytes(b'older release')
    return tmp_path, folder


def snapshot(folder):
    return {path.relative_to(folder): path.read_bytes()
            for path in folder.rglob('*') if path.is_file()}


def fake_pyinstaller(command, cwd, check):
    assert check is True
    assert '--noconfirm' in command
    staged = Path(command[command.index('--distpath') + 1])
    assert staged.is_relative_to(cwd / 'build')
    folder = staged / 'LensTracker'
    (folder / '_internal').mkdir(parents=True)
    (folder / 'LensTracker.exe').write_bytes(b'new executable')
    (folder / '_internal' / 'new.dll').write_bytes(b'new library')


def test_build_preserves_live_data_and_archives_only_clean_distribution(build_workspace, monkeypatch):
    root, folder = build_workspace
    before = snapshot(folder / 'data')
    (folder / 'desktop.log.1').write_bytes(b'private old log outside data')
    monkeypatch.setattr(build_desktop.subprocess, 'run', fake_pyinstaller)
    make_archive = Mock(wraps=build_desktop.archive_distribution)
    monkeypatch.setattr(build_desktop, 'archive_distribution', make_archive)

    build_desktop.main()

    assert snapshot(folder / 'data') == before
    assert (root / build_desktop.LAUNCHER_NAME).read_bytes() == b'new launcher'
    assert (folder / 'LensTracker.exe').read_bytes() == b'new executable'
    assert not (folder / '_internal' / 'old.dll').exists()
    assert (folder / '_internal' / 'new.dll').read_bytes() == b'new library'
    assert (folder / 'CHANGELOG.md').read_text(encoding='utf-8') == 'CHANGELOG.md'
    assert make_archive.call_count == 1
    assert (root / 'dist' / 'LensTracker-3.2.1-Windows-x64.zip').read_bytes() == (
        root / 'dist' / 'LensTracker-Windows-x64.zip').read_bytes()
    assert (root / 'dist' / 'LensTracker-1.0.0-Windows-x64.zip').read_bytes() == b'older release'
    with zipfile.ZipFile(root / 'dist' / 'LensTracker-Windows-x64.zip') as archive:
        assert archive.read('LensTracker/LensTracker.exe') == b'new executable'
        assert 'LensTracker/_internal/licenses/license.txt' in archive.namelist()
        assert 'LensTracker/CHANGELOG.md' in archive.namelist()
        assert not any('/data/' in name.casefold() or 'desktop.log' in name.casefold()
                       for name in archive.namelist())


@pytest.mark.parametrize('failure', ['build', 'archive'])
def test_failed_staged_build_leaves_existing_installation_untouched(build_workspace, monkeypatch, failure):
    root, folder = build_workspace
    before = snapshot(root / 'dist')

    def fail_build(command, cwd, check):
        fake_pyinstaller(command, cwd, check)
        raise subprocess.CalledProcessError(1, command)

    def fail_archive(*args, **kwargs):
        raise OSError('Cannot create archive')

    monkeypatch.setattr(build_desktop.subprocess, 'run',
                        fail_build if failure == 'build' else fake_pyinstaller)
    if failure == 'archive':
        monkeypatch.setattr(build_desktop, 'archive_distribution', fail_archive)

    with pytest.raises((subprocess.CalledProcessError, OSError)):
        build_desktop.main()

    assert snapshot(root / 'dist') == before
    assert (root / build_desktop.LAUNCHER_NAME).read_bytes() == b'old launcher'


@pytest.mark.parametrize('failure_target', ['_internal', 'LensTracker-3.2.1-Windows-x64.zip',
                                           'LensTracker-Windows-x64.zip', build_desktop.LAUNCHER_NAME])
def test_failed_install_restores_old_binaries_and_both_archives(build_workspace, monkeypatch, failure_target):
    root, folder = build_workspace
    before = snapshot(root / 'dist')
    monkeypatch.setattr(build_desktop.subprocess, 'run', fake_pyinstaller)
    original_rename = Path.rename

    def fail_library_install(source, target):
        destination = folder / failure_target if failure_target == '_internal' else root / 'dist' / failure_target
        if failure_target == build_desktop.LAUNCHER_NAME:
            destination = root / failure_target
        if (source.name == failure_target and Path(target) == destination
                and source.is_relative_to(root / 'build') and source.parent.name != 'previous'):
            raise PermissionError('Simulated file lock during installation')
        return original_rename(source, target)

    monkeypatch.setattr(Path, 'rename', fail_library_install)

    with pytest.raises(PermissionError):
        build_desktop.main()

    assert snapshot(root / 'dist') == before
    assert (root / build_desktop.LAUNCHER_NAME).read_bytes() == b'old launcher'


def test_archive_excludes_data_case_insensitively_and_old_state_locations(build_workspace):
    root, folder = build_workspace
    for relative in ('DATA/metadata.json', '_internal/DaTa/private.txt', 'window.ini',
                     'lenstracker.sqlite3', 'lenstracker.sqlite3-shm', 'library.sqlite3-wal',
                     'desktop.log.2', '_internal/private.log'):
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'private')

    archive_path = build_desktop.archive_distribution()

    assert archive_path == root / 'dist' / 'LensTracker-3.2.1-Windows-x64.zip'
    assert archive_path.read_bytes() == (root / 'dist' / 'LensTracker-Windows-x64.zip').read_bytes()
    assert (root / 'dist' / 'LensTracker-1.0.0-Windows-x64.zip').read_bytes() == b'older release'
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            'LensTracker/LensTracker.exe', 'LensTracker/_internal/old.dll',
            'LensTracker/DESKTOP.md', 'LensTracker/THIRD_PARTY_NOTICES.md',
            'LensTracker/VALIDATION.md', 'LensTracker/_internal/README.md',
            'LensTracker/_internal/licenses/license.txt', 'LensTracker/CHANGELOG.md',
        }


def test_build_rejects_paths_outside_workspace(build_workspace, tmp_path):
    root, folder = build_workspace
    with pytest.raises(ValueError, match='inside the workspace'):
        build_desktop.workspace_path(root / '..' / 'other-installation')


def test_spec_embeds_central_version_in_windows_resource_and_includes_changelog(build_workspace):
    versioninfo = pytest.importorskip('PyInstaller.utils.win32.versioninfo')
    root, folder = build_workspace
    analysis = Mock(return_value=SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]))
    executable = Mock()

    runpy.run_path(str(SPEC), init_globals={
        'SPECPATH': str(root), 'Analysis': analysis, 'PYZ': Mock(),
        'EXE': executable, 'COLLECT': Mock(),
    })

    embedded = executable.call_args.kwargs['version']
    decoded = versioninfo.VSVersionInfo()
    decoded.fromRaw(embedded.toRaw())
    assert decoded.ffi.fileVersionMS == (3 << 16) | 2
    assert decoded.ffi.fileVersionLS == 1 << 16
    assert decoded.ffi.productVersionMS == decoded.ffi.fileVersionMS
    assert decoded.ffi.productVersionLS == decoded.ffi.fileVersionLS
    strings = {item.name: item.val for item in decoded.kids[0].kids[0].kids}
    assert strings['FileVersion'] == strings['ProductVersion'] == '3.2.1'
    assert strings['OriginalFilename'] == 'LensTracker.exe'
    assert ('CHANGELOG.md', '.') in analysis.call_args.kwargs['datas']
