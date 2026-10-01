import re
import sqlite3
import sys
from pathlib import Path

import pytest

from app import create_app
from lenstracker.database import initialize, connect, equipment_id
from lenstracker.runtime import data_directory, legacy_candidates, migrate_legacy


def test_desktop_data_is_next_to_executable_and_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv('LENSTRACKER_DATA_DIR', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    executable = tmp_path / 'application' / 'LensTracker.exe'
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'old'))
    assert data_directory() == executable.parent / 'data'
    assert legacy_candidates()[0] == tmp_path / 'old' / 'LensTracker' / 'lenstracker.sqlite3'
    monkeypatch.setenv('LENSTRACKER_DATA_DIR', str(tmp_path / 'custom'))
    assert data_directory() == tmp_path / 'custom'


def test_source_data_is_in_project_folder(tmp_path, monkeypatch):
    monkeypatch.delenv('LENSTRACKER_DATA_DIR', raising=False)
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.chdir(tmp_path)
    assert data_directory() == Path(__file__).resolve().parent.parent / 'data'


def test_legacy_backup_includes_uncheckpointed_wal_and_never_overwrites(tmp_path):
    source = tmp_path / 'old.sqlite3'
    target = tmp_path / 'new' / 'library.sqlite3'
    initialize(source)
    # Keep a connection open so committed records can remain in the WAL.
    original = sqlite3.connect(source)
    original.execute('INSERT INTO equipment(kind,model,name,new_price) VALUES(?,?,?,?)',
                     ('camera','Original','Original',2300000))
    original.commit()
    try:
        assert migrate_legacy(target, [source]) == source
        with connect(target) as db:
            assert db.execute('SELECT new_price FROM equipment').fetchone()[0] == 2300000
            db.execute('UPDATE equipment SET new_price=1900000')
        assert migrate_legacy(target, [source]) is None
        with connect(target) as db:
            assert db.execute('SELECT new_price FROM equipment').fetchone()[0] == 1900000
        assert original.execute('SELECT new_price FROM equipment').fetchone()[0] == 2300000
    finally:
        original.close()


def test_invalid_legacy_database_does_not_create_destination(tmp_path):
    source = tmp_path / 'broken.sqlite3'
    source.write_bytes(b'not a database')
    target = tmp_path / 'new.sqlite3'
    with pytest.raises(sqlite3.DatabaseError):
        migrate_legacy(target, [source])
    assert not target.exists()


def test_legacy_folder_and_window_settings_are_copied_without_overwriting(tmp_path):
    source = tmp_path / 'old' / 'lenstracker.sqlite3'
    target = tmp_path / 'portable' / 'lenstracker.sqlite3'
    initialize(source)
    settings = source.parent / 'window.ini'
    settings.write_text('[General]\nlast_folder=H:/Picture\n', encoding='utf-8')
    assert migrate_legacy(target, [source]) == source
    restored = target.parent / 'window.ini'
    assert restored.read_bytes() == settings.read_bytes()
    restored.write_text('[General]\nlast_folder=I:/Photos\n', encoding='utf-8')
    assert migrate_legacy(target, [source]) is None
    assert 'I:/Photos' in restored.read_text(encoding='utf-8')
    assert 'H:/Picture' in settings.read_text(encoding='utf-8')


def test_folder_picker_only_available_in_desktop_and_requires_token(tmp_path):
    selected = []
    def choose():
        selected.append(True)
        return str(tmp_path)
    app = create_app(tmp_path / 'library.sqlite3', choose_folder=choose)
    client = app.test_client()
    html = client.get('/').text
    assert 'id="choose-folder"' in html and '폴더 찾아보기' in html
    token = re.search(r'name="lens-token" content="([^"]+)"', html).group(1)
    assert client.post('/api/desktop/folder').status_code == 403
    assert not selected
    response = client.post('/api/desktop/folder',headers={'X-LensTracker-Token':token})
    assert response.get_json()['folder'] == str(tmp_path)
    assert selected == [True]
    web = create_app(tmp_path / 'library.sqlite3').test_client()
    web_html = web.get('/').text
    assert 'id="choose-folder"' not in web_html
    web_token = re.search(r'name="lens-token" content="([^"]+)"', web_html).group(1)
    assert web.post('/api/desktop/folder',headers={'X-LensTracker-Token':web_token}).status_code == 404
