import csv
import io
import os
import re
import shutil
import threading
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from app import create_app
from lenstracker.analytics import report
from lenstracker.database import connect, equipment_id, initialize, recent_folders, remember_folder
from lenstracker.metadata import read_metadata
from lenstracker.scanner import Scanner


def photo(path, model='ILCE-7M4', lens='FE 35mm F1.4 GM', date='2026:09:01 12:30:45', color='red'):
    exif = Image.Exif()
    exif[271] = 'SONY'
    exif[272] = model
    exif[34665] = {42036: lens, 36867: date, 37386: IFDRational(35),
                   33437: IFDRational(14, 10), 34855: 400}
    Image.new('RGB', (16, 16), color).save(path, exif=exif)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'library.sqlite'
    initialize(path)
    return path


@pytest.fixture
def client(db):
    app = create_app(db)
    app.config['TESTING'] = True
    client = app.test_client()
    token = re.search(r'name="lens-token" content="([^"]+)"', client.get('/').text).group(1)
    client.environ_base['HTTP_X_LENSTRACKER_TOKEN'] = token
    return client


def seed(db):
    with connect(db) as conn:
        camera = equipment_id(conn, 'camera', 'Test camera')
        lens = equipment_id(conn, 'lens', 'Test lens')
        lens2 = equipment_id(conn, 'lens', 'Second lens')
        conn.execute('UPDATE equipment SET new_price=2000000 WHERE id=?', (camera,))
        conn.execute('UPDATE equipment SET new_price=1000000 WHERE id=?', (lens,))
        for i in range(100):
            conn.execute('INSERT INTO photos(digest,camera_id,lens_id,taken_at,format) VALUES(?,?,?,?,?)',
                         (str(i), camera, lens, '2026-09-01T12:30:00', 'JPEG'))
    return camera, lens, lens2


def test_real_exif(tmp_path):
    path = tmp_path / '촬영.jpg'
    photo(path)
    result = read_metadata(path)
    assert result == dict(camera='SONY ILCE-7M4', lens='FE 35mm F1.4 GM',
                          taken_at='2026-09-01T12:30:45', focal=35.0, aperture=1.4, iso=400.0, format='JPEG')


def test_deduplicate_rescan_changed_path(db, tmp_path):
    path = tmp_path / 'a.jpg'
    photo(path)
    copy = tmp_path / 'copy.jpg'
    shutil.copy2(path, copy)
    scanner = Scanner(db)
    assert scanner.import_file(path, None) == 'added'
    assert scanner.import_file(copy, None) == 'duplicates'
    assert scanner.import_file(path, None) == 'skipped'
    assert report(db, {})['summary']['total'] == 1
    photo(path, model='ILCE-9', color='blue')
    assert scanner.import_file(path, None) == 'added'
    assert report(db, {})['summary']['total'] == 2
    photo(copy, model='ILCE-9', color='blue')
    assert scanner.import_file(copy, None) == 'duplicates'
    assert report(db, {})['summary']['total'] == 1


def test_cached_metadata_survives_restart_without_reading_photo(db, tmp_path, monkeypatch):
    path = tmp_path / 'cached.jpg'
    photo(path)
    assert Scanner(db).import_file(path, None) == 'added'
    initial_mtime = path.stat().st_mtime_ns
    original_open = Path.open

    def guarded_open(self, *args, **kwargs):
        assert self.resolve() != path.resolve(), 'Cached photo bytes must not be opened'
        return original_open(self, *args, **kwargs)

    def unexpected_metadata(*args, **kwargs):
        pytest.fail('Cached photo EXIF must not be read again')

    with monkeypatch.context() as cached:
        cached.setattr(Path, 'open', guarded_open)
        cached.setattr('lenstracker.scanner.read_metadata', unexpected_metadata)
        restarted = create_app(db)
        scanner = restarted.extensions['scanner']
        assert scanner.snapshot()['status'] == 'idle'
        assert restarted.test_client().get('/api/report').get_json()['summary']['total'] == 1
        assert scanner.import_file(path, None) == 'skipped'

    photo(path, model='ILCE-9', color='blue')
    os.utime(path, ns=(initial_mtime + 1_000_000_000, initial_mtime + 1_000_000_000))
    reads = []

    def recorded_metadata(photo_path, executable):
        reads.append(photo_path)
        return read_metadata(photo_path, executable)

    monkeypatch.setattr('lenstracker.scanner.read_metadata', recorded_metadata)
    assert scanner.import_file(path, None) == 'added'
    assert reads == [path.resolve()]
    data = report(db, {})
    assert data['summary']['total'] == 1
    assert data['rankings']['camera'][0]['name'] == 'SONY ILCE-9'


def test_folder_history_persists_deduplicates_and_moves_reopened_folder_first(db, tmp_path):
    first, second = tmp_path / 'First', tmp_path / 'Second'
    first.mkdir()
    second.mkdir()
    assert remember_folder(db, first / '..' / 'First') == str(first.resolve())
    remember_folder(db, second)
    with connect(db) as conn:
        conn.execute("UPDATE folders SET last_opened_at='2020-01-01T00:00:00+00:00'")
    # Spelling aliases, including Windows case variants, must update the existing row.
    alias = str(first).swapcase() if os.name == 'nt' else str(first / '..' / 'First')
    remember_folder(db, alias)
    saved = recent_folders(db)
    assert len(saved) == 2
    assert os.path.normcase(saved[0]['path']) == os.path.normcase(str(first.resolve()))
    assert saved[0]['last_opened_at'] > saved[1]['last_opened_at']
    restarted = create_app(db)
    response = restarted.test_client().get('/api/folders')
    assert response.status_code == 200
    assert response.get_json() == dict(folders=saved, last_folder=saved[0]['path'],
                                       data_directory=str(db.parent.resolve()))
    assert restarted.extensions['scanner'].snapshot()['status'] == 'idle'
    with connect(db) as conn:
        conn.execute("UPDATE folders SET last_opened_at='2020-01-01T00:00:00+00:00'")
    assert [os.path.normcase(row['path']) for row in recent_folders(db)] == sorted(
        os.path.normcase(str(folder.resolve())) for folder in (first, second))


def test_empty_folder_history_and_offline_previously_opened_folder(client, db, tmp_path):
    assert client.get('/api/folders').get_json() == dict(
        folders=[], last_folder='', data_directory=str(db.parent.resolve()))
    offline = tmp_path / 'offline-drive' / 'old-photos'
    remember_folder(db, offline)
    assert client.get('/api/folders').get_json()['last_folder'] == str(offline.resolve())


def test_scan_records_only_accepted_existing_folders(client, db, tmp_path, monkeypatch):
    accepted, busy = tmp_path / 'accepted', tmp_path / 'busy'
    accepted.mkdir()
    busy.mkdir()
    entered, release = threading.Event(), threading.Event()

    def blocked_run(folder):
        entered.set()
        release.wait(5)

    monkeypatch.setattr(client.application.extensions['scanner'], 'run', blocked_run)
    for folder in ('', str(tmp_path / 'missing'), str(db)):
        assert client.post('/api/scan', json={'folder': folder}).status_code == 400
    assert recent_folders(db) == []
    assert client.post('/api/scan', json={'folder': str(accepted)},
                       headers={'X-LensTracker-Token': 'bad'}).status_code == 403
    assert recent_folders(db) == []
    assert client.post('/api/scan', json={'folder': str(accepted)}).status_code == 202
    assert entered.wait(2)
    try:
        saved = recent_folders(db)
        assert [row['path'] for row in saved] == [str(accepted.resolve())]
        assert client.post('/api/scan', json={'folder': str(busy)}).status_code == 409
        assert recent_folders(db) == saved
        assert create_app(db).test_client().get('/api/folders').get_json()['last_folder'] == str(accepted.resolve())
    finally:
        release.set()


@pytest.mark.parametrize('selection', [None, '', 'missing', 'valid'])
def test_folder_picker_history_only_records_successful_selection(db, tmp_path, selection):
    folder = tmp_path / 'selected'
    folder.mkdir()
    chosen = str(folder) if selection == 'valid' else str(folder / 'missing') if selection == 'missing' else selection
    app = create_app(db, choose_folder=lambda: chosen)
    client = app.test_client()
    token = re.search(r'name="lens-token" content="([^"]+)"', client.get('/').text).group(1)
    response = client.post('/api/desktop/folder', headers={'X-LensTracker-Token': token})
    assert response.status_code == (400 if selection == 'missing' else 200)
    if selection == 'valid':
        assert response.get_json()['folder'] == str(folder.resolve())
        assert recent_folders(db)[0]['path'] == str(folder.resolve())
        assert app.extensions['scanner'].snapshot()['status'] == 'idle'
    else:
        assert recent_folders(db) == []


def test_folder_picker_error_does_not_record_history(db):
    def unavailable_picker():
        raise RuntimeError('picker unavailable')

    app = create_app(db, choose_folder=unavailable_picker)
    app.config['TESTING'] = True
    client = app.test_client()
    token = re.search(r'name="lens-token" content="([^"]+)"', client.get('/').text).group(1)
    with pytest.raises(RuntimeError, match='picker unavailable'):
        client.post('/api/desktop/folder', headers={'X-LensTracker-Token': token})
    assert recent_folders(db) == []


def test_missing_exif_and_corrupt_files(db, tmp_path, monkeypatch):
    monkeypatch.setattr('lenstracker.scanner.exiftool_path', lambda: None)
    folder = tmp_path / 'images'
    folder.mkdir()
    Image.new('RGB', (16,16)).save(folder / 'no-exif.jpg')
    (folder / 'broken.jpg').write_bytes(b'bad image')
    (folder / 'test.nef').write_bytes(b'raw placeholder')
    scanner = Scanner(db)
    scanner.run(folder)
    state = scanner.snapshot()
    assert state['status'] == 'completed'
    assert (state['added'], state['failed'], state['processed']) == (1,2,3)
    summary = report(db, {})['summary']
    assert summary['total'] == summary['missing_camera'] == summary['missing_lens'] == summary['missing_date'] == 1
    assert report(db, {'start':'2026-01-01'})['summary']['total'] == 0


def test_recursive_scan_includes_nested_photos_and_deduplicates(db, tmp_path, monkeypatch):
    monkeypatch.setattr('lenstracker.scanner.exiftool_path', lambda: None)
    folder = tmp_path / 'root'
    deepest = folder / 'year' / 'month' / 'day'
    deepest.mkdir(parents=True)
    photo(folder / 'root.jpg')
    photo(folder / 'year' / 'year.jpg', color='blue')
    photo(deepest / 'deep.jpg', color='green')
    shutil.copy2(folder / 'root.jpg', deepest / 'duplicate.jpg')
    (deepest / 'not-an-image.txt').write_text('skip me')
    scanner = Scanner(db)
    scanner.run(folder)
    state = scanner.snapshot()
    assert (state['added'], state['duplicates'], state['failed']) == (3, 1, 0)
    assert report(db, {})['summary']['total'] == 3


def test_costs_filters_and_partial_price_coverage(db):
    camera, lens, lens2 = seed(db)
    data = report(db, {})
    assert data['rankings']['camera'][0]['cost'] == 20000
    assert data['rankings']['lens'][0]['cost'] == 10000
    assert data['combinations'][0]['cost'] == 30000
    assert data['summary']['average_cost'] == 30000
    with connect(db) as conn:
        conn.execute('INSERT INTO photos(digest,camera_id,lens_id,taken_at,format) VALUES(?,?,?,?,?)',
                     ('extra',camera,lens2,'2026-08-31T23:59:59','JPEG'))
    data = report(db, {})
    assert data['summary']['covered'] == 100
    assert data['summary']['coverage'] == pytest.approx(100/101)
    assert data['summary']['average_cost'] == pytest.approx(2000000/101+10000)
    assert data['combinations'][1]['cost'] is None
    filtered = report(db, {'start':'2026-09-01','end':'2026-09-01','camera':str(camera),'lens':str(lens)})
    assert filtered['summary']['total'] == 100
    assert filtered['rankings']['camera'][0]['cost'] == 20000
    assert filtered['summary']['average_cost'] == pytest.approx(2000000/101+10000)
    with connect(db) as conn:
        conn.execute('UPDATE equipment SET new_price=0 WHERE id=?', (lens2,))
    assert report(db, {})['summary']['covered'] == 101
    assert report(db, {'lens':str(lens2)})['rankings']['lens'][0]['cost'] == 0


@pytest.mark.parametrize('price', [-1, 1.5, True, '1000', 100000000001])
def test_reject_invalid_price(client, db, price):
    camera, _, _ = seed(db)
    assert client.patch(f'/api/equipment/{camera}', json={'new_price':price}).status_code == 400


def test_update_persistence_and_csv(client, db):
    camera, _, _ = seed(db)
    assert client.patch(f'/api/equipment/{camera}', json={'name':'=SUM(1,2)', 'purchase_price':1200000, 'note':'공식 판매처'}).status_code == 200
    restarted = create_app(db).test_client()
    equipment = restarted.get('/api/report').get_json()['equipment']
    assert next(e for e in equipment if e['id']==camera)['purchase_cost'] == 12000
    response = client.get('/api/export?start=2026-09-01&end=2026-09-01')
    assert response.data.startswith(b'\xef\xbb\xbf')
    exported = list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))
    assert exported[1][1] == "'=SUM(1,2)"
    assert exported[1][2] == '100'
    assert float(exported[3][5]) == 30000
    assert len(list(csv.reader(io.StringIO(client.get('/api/export?start=2027-01-01').data.decode('utf-8-sig'))))) == 1


def test_request_security_and_validation(client):
    assert client.post('/api/scan', json={'folder':'.'}, headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/api/scan', json={'folder':'.'}, headers={'X-LensTracker-Token':'bad'}).status_code == 403
    assert client.get('/api/report', headers={'Host':'evil.example'}).status_code == 400
    assert client.post('/api/scan', json={}).status_code == 400
    assert client.post('/api/scan', json=[]).status_code == 400
    assert client.get('/api/report?start=garbage').status_code == 400
    assert client.get('/api/report?start=2026-09-02&end=2026-09-01').status_code == 400
    assert client.get('/api/photos?page=nope').status_code == 400


def test_photo_pagination_and_unknown_filter(client, db):
    camera, _, _ = seed(db)
    assert len(client.get('/api/photos').get_json()['photos']) == 50
    assert client.get('/api/photos?page=999').get_json()['page'] == 2
    assert client.get('/api/photos?camera=unknown').get_json()['total'] == 0
    assert client.get('/api/photos?camera='+str(camera)).get_json()['total'] == 100


def test_scan_concurrency_and_cancellation(db, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    scanner = Scanner(db)
    def blocked_run(folder):
        entered.set()
        release.wait(5)
    monkeypatch.setattr(scanner, 'run', blocked_run)
    scanner.start(tmp_path)
    assert entered.wait(2)
    try:
        with pytest.raises(RuntimeError):
            scanner.start(tmp_path)
        scanner.cancel()
        assert scanner.snapshot()['status'] == 'cancelling'
        assert scanner.cancelled.is_set()
    finally:
        release.set()


def test_exiftool_json_adapter(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from lenstracker import metadata
    def fake_run(args, **kwargs):
        assert '-json' in args and kwargs['timeout'] == 45
        return SimpleNamespace(stdout='[{"Make":"Canon","Model":"Canon EOS R6","LensModel":"RF 35mm F1.8","DateTimeOriginal":"2026:09:01 12:30:00","FocalLength":35,"FNumber":1.8,"ISO":100}]',returncode=0,stderr='')
    monkeypatch.setattr(metadata.subprocess, 'run', fake_run)
    result = read_metadata(tmp_path/'image.cr3', 'exiftool')
    assert result['camera'] == 'Canon EOS R6'
    assert result['lens'] == 'RF 35mm F1.8'
    assert result['format'] == 'CR3'
