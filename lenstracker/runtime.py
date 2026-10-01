"""Portable application data and migration from older desktop libraries."""
import os
import shutil
import sqlite3
import sys
from pathlib import Path


def application_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_directory():
    override = os.environ.get('LENSTRACKER_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    return application_directory() / 'data'


def legacy_candidates():
    previous = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'LensTracker'
    candidates = [previous / 'lenstracker.sqlite3']
    if not getattr(sys, 'frozen', False):
        return candidates
    executable = application_directory()
    # A locally built dist/LensTracker distribution can find the previous web app.
    project = executable.parent.parent
    if (project / 'app.py').is_file() and (project / 'lenstracker').is_dir():
        candidates.append(project / 'data' / 'lenstracker.sqlite3')
    return candidates


def migrate_legacy(destination, candidates):
    """Copy a consistent SQLite snapshot once; never change or replace a library."""
    destination = Path(destination)
    if destination.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        source = Path(candidate).resolve()
        if not source.is_file() or source == destination.resolve():
            continue
        temporary = destination.with_suffix('.migrating.sqlite3')
        try:
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
                # Validate the old application schema before adopting a file.
                original.execute('SELECT id FROM photos LIMIT 1')
                original.execute('SELECT new_price FROM equipment LIMIT 1')
                backup = sqlite3.connect(temporary)
                try:
                    original.backup(backup)
                    if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('기존 라이브러리 복사본 검증에 실패했습니다.')
                finally:
                    backup.close()
            temporary.rename(destination)
            settings = source.parent / 'window.ini'
            target_settings = destination.parent / 'window.ini'
            if settings.is_file() and not target_settings.exists():
                shutil.copy2(settings, target_settings)
            return source
        finally:
            if temporary.exists():
                temporary.unlink()
    return None
