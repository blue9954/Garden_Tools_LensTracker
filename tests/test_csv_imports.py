import csv
import io
import re
import shutil
import sqlite3

import pytest

from app import create_app
from lenstracker import csv_imports
from lenstracker.csv_imports import HEADERS, get_csv_import, import_stats_csv, list_csv_imports, read_stats_csv
from lenstracker.database import connect, equipment_id, initialize


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'application' / 'lenstracker.sqlite3'
    initialize(path)
    return path


def authorized_client(application):
    client = application.test_client()
    token = re.search(r'name="lens-token" content="([^"]+)"', client.get('/').text).group(1)
    client.environ_base['HTTP_X_LENSTRACKER_TOKEN'] = token
    return client


def csv_file(tmp_path, rows, *, filename='통계.csv', headers=HEADERS, encoding='utf-8-sig'):
    text = io.StringIO(newline='')
    writer = csv.writer(text)
    writer.writerow(headers)
    writer.writerows(rows)
    path = tmp_path / filename
    path.write_bytes(text.getvalue().encode(encoding))
    return path


def import_rows(database):
    with connect(database) as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('csv_imports', 'csv_import_rows')}


def library_rows(database):
    with connect(database) as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')]
                for table in ('equipment', 'photos', 'files', 'folders')}


def test_export_roundtrip_preserves_stats_and_entire_existing_photo_library(database, tmp_path):
    with connect(database) as db:
        camera = equipment_id(db, 'camera', '원래 카메라 이름')
        camera2 = equipment_id(db, 'camera', '카메라 α, "A"')
        lens = equipment_id(db, 'lens', '무료 렌즈')
        lens2 = equipment_id(db, 'lens', '가격 미정')
        lens3 = equipment_id(db, 'lens', '최대 가격 + 렌즈')
        db.execute('UPDATE equipment SET name=?,new_price=0,purchase_price=700,note=? WHERE id=?',
                   ('=SUM(1,2)', '개인 메모 보존', camera))
        db.execute('UPDATE equipment SET new_price=0 WHERE id=?', (lens,))
        db.execute('UPDATE equipment SET new_price=100000000000 WHERE id IN (?,?)', (camera2, lens3))
        for index, (body, glass) in enumerate(((camera, lens), (camera2, lens2), (camera2, lens3))):
            identifier = db.execute(
                'INSERT INTO photos(digest,camera_id,lens_id,taken_at,format) VALUES(?,?,?,?,?)',
                (f'existing-{index}', body, glass, '2026-09-12T10:11:12', 'JPEG')).lastrowid
            db.execute('INSERT INTO files(path,size,mtime,photo_id) VALUES(?,?,?,?)',
                       (str(tmp_path / f'original-{index}.jpg'), 1024, 12345, identifier))
    app = create_app(database)
    client = authorized_client(app)
    original_library = library_rows(database)
    original_report = client.get('/api/report').get_json()
    source = tmp_path / '내보낸 통계.csv'
    source.write_bytes(client.get('/api/export').data)
    original_content = source.read_bytes()
    original_mtime = source.stat().st_mtime_ns

    response = client.post('/api/csv-imports', json={'path': str(source)})
    assert response.status_code == 201
    imported = response.get_json()['import']
    assert imported['total_photos'] == 3
    assert imported['row_count'] == 8
    assert imported['counts'] == dict(camera=2, lens=3, combination=3)
    assert imported['filename'] == source.name
    assert imported['source_path'] == str(source.resolve())
    rows = imported['rows']
    escaped_camera = next(row for row in rows if row['kind'] == 'camera' and row['name'].startswith("'="))
    assert escaped_camera == dict(kind='camera', name="'=SUM(1,2)", count=1,
                                  share_percent=33.33, new_price=0, cost=0)
    assert any(row['new_price'] is None and row['cost'] is None for row in rows)
    assert any(row['kind'] == 'combination' and row['new_price'] == 200_000_000_000 for row in rows)
    assert library_rows(database) == original_library
    assert client.get('/api/report').get_json() == original_report
    assert source.read_bytes() == original_content
    assert source.stat().st_mtime_ns == original_mtime

    # Stored statistics remain available after both application restart and source removal.
    source.unlink()
    restarted = create_app(database).test_client()
    assert restarted.get(f"/api/csv-imports/{imported['id']}").get_json() == imported
    summary = {key: value for key, value in imported.items() if key != 'rows'}
    assert restarted.get('/api/csv-imports').get_json() == {'imports': [summary]}


def test_empty_export_is_valid_snapshot(database, tmp_path):
    client = authorized_client(create_app(database))
    source = tmp_path / 'empty.csv'
    source.write_bytes(client.get('/api/export').data)
    imported = import_stats_csv(database, source)['import']
    assert imported['total_photos'] == imported['row_count'] == 0
    assert imported['rows'] == []
    assert imported['counts'] == dict(camera=0, lens=0, combination=0)
    assert get_csv_import(database, imported['id']) == imported


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'cp949'])
def test_supported_encodings_keep_names_and_supplied_values(database, tmp_path, encoding):
    source = csv_file(tmp_path, [
        ['조합', '  카메라 + 렌즈, "한글"  ', '2', '12.34', '0', '0'],
        ['렌즈', "'이미 따옴표가 있는 이름", '2', '50', '', ''],
        ['카메라', '=기록 이름', '2', '66.67', '100', '7.89'],
    ], encoding=encoding)
    imported = import_stats_csv(database, source)['import']
    assert [row['kind'] for row in imported['rows']] == ['camera', 'lens', 'combination']
    assert imported['rows'][0] == dict(kind='camera', name='=기록 이름', count=2,
                                       share_percent=66.67, new_price=100, cost=7.89)
    assert imported['rows'][1]['name'] == "'이미 따옴표가 있는 이름"
    assert imported['rows'][1]['new_price'] is imported['rows'][1]['cost'] is None
    assert imported['rows'][2]['name'] == '  카메라 + 렌즈, "한글"  '


def test_duplicate_content_from_another_path_is_not_reimported(database, tmp_path):
    source = csv_file(tmp_path, [['카메라', '카메라', 3, 100, '', '']])
    first = import_stats_csv(database, source)
    copy = tmp_path / 'renamed.CSV'
    shutil.copyfile(source, copy)
    duplicate = import_stats_csv(database, copy)
    assert duplicate == {'duplicate': True, 'import': first['import']}
    assert len(list_csv_imports(database)) == 1
    assert len(import_rows(database)['csv_import_rows']) == 1


def test_distinct_snapshots_stay_separate_and_preserve_repeated_labels(database, tmp_path):
    first = import_stats_csv(database, csv_file(tmp_path, [
        ['카메라', '같은 이름', 1, 25, '', ''],
        ['카메라', '같은 이름', 3, 75, '', ''],
        ['렌즈', '렌즈', 4, 100, '', ''],
    ]))['import']
    second = import_stats_csv(database, csv_file(tmp_path, [
        ['카메라', '다른 통계', 7, 100, '', ''],
    ], filename='second.csv'))['import']
    assert first['total_photos'] == 4
    assert [row['count'] for row in first['rows']] == [3, 1, 4]
    assert second['total_photos'] == 7
    assert [item['id'] for item in list_csv_imports(database)] == [second['id'], first['id']]
    assert library_rows(database)['photos'] == []
    assert library_rows(database)['equipment'] == []


@pytest.mark.parametrize('bad_row', [
    ['카메라', '열 부족', 1, 100, ''],
    ['카메라', '열 초과', 1, 100, '', '', '추가'],
    ['알 수 없는 유형', '장비', 1, 100, '', ''],
    ['카메라', ' ', 1, 100, '', ''],
    ['카메라', '너무 긴 이름' * 201, 1, 100, '', ''],
    ['카메라', '장비', -1, 100, '', ''],
    ['카메라', '장비', '1.5', 100, '', ''],
    ['카메라', '장비', 'NaN', 100, '', ''],
    ['카메라', '장비', 1_000_000_000_001, 100, '', ''],
    ['카메라', '장비', 1, '', '', ''],
    ['카메라', '장비', 1, 100.01, '', ''],
    ['카메라', '장비', 1, -0.01, '', ''],
    ['카메라', '장비', 1, 'Infinity', '', ''],
    ['카메라', '장비', 1, 100, -1, ''],
    ['카메라', '장비', 1, 100, 1.5, ''],
    ['카메라', '장비', 1, 100, 100_000_000_001, ''],
    ['조합', '장비', 1, 100, 200_000_000_001, ''],
    ['카메라', '장비', 1, 100, '', -1],
    ['카메라', '장비', 1, 100, '', 'nan'],
    ['카메라', '장비', 1, 100, '', '1e1000'],
])
def test_invalid_rows_do_not_partially_import_or_change_existing_snapshot(database, tmp_path, bad_row):
    source = csv_file(tmp_path, [['카메라', '기존', 1, 100, '', '']])
    import_stats_csv(database, source)
    before = import_rows(database)
    invalid = csv_file(tmp_path, [['카메라', '정상 첫 행', 1, 100, '', ''], bad_row], filename='invalid.csv')
    with pytest.raises(ValueError):
        import_stats_csv(database, invalid)
    assert import_rows(database) == before


@pytest.mark.parametrize('headers', [HEADERS[:-1], list(reversed(HEADERS)), ['Unknown'] * 6])
def test_non_export_headers_are_rejected(database, tmp_path, headers):
    source = csv_file(tmp_path, [], headers=headers)
    with pytest.raises(ValueError, match='열 이름'):
        import_stats_csv(database, source)
    assert list_csv_imports(database) == []


def test_inconsistent_group_totals_are_rejected(database, tmp_path):
    source = csv_file(tmp_path, [
        ['카메라', '카메라', 5, 100, '', ''],
        ['렌즈', '렌즈', 4, 100, '', ''],
    ])
    with pytest.raises(ValueError, match='합계'):
        import_stats_csv(database, source)
    assert list_csv_imports(database) == []


def test_malformed_quotes_and_invalid_encoding_abort(database, tmp_path):
    source = tmp_path / 'broken.csv'
    source.write_bytes((','.join(HEADERS) + '\n카메라,"닫히지 않은 따옴표,1,100,,').encode('utf-8-sig'))
    with pytest.raises(ValueError, match='CSV 형식'):
        import_stats_csv(database, source)
    source.write_bytes(b'\xff')
    with pytest.raises(ValueError, match='인코딩'):
        import_stats_csv(database, source)
    assert list_csv_imports(database) == []


def test_size_and_row_limits_before_import(database, tmp_path, monkeypatch):
    source = csv_file(tmp_path, [['카메라', '장비', 1, 100, '', '']] * 3)
    with monkeypatch.context() as limit:
        limit.setattr(csv_imports, 'MAX_BYTES', source.stat().st_size - 1)
        with pytest.raises(ValueError, match='10MB'):
            import_stats_csv(database, source)
    monkeypatch.setattr(csv_imports, 'MAX_ROWS', 2)
    with pytest.raises(ValueError, match='100,000행'):
        import_stats_csv(database, source)
    assert list_csv_imports(database) == []


def test_database_error_rolls_back_snapshot_and_all_rows(database, tmp_path):
    source = csv_file(tmp_path, [
        ['카메라', '카메라', 1, 100, '', ''],
        ['렌즈', '렌즈', 1, 100, '', ''],
    ])
    with connect(database) as db:
        db.execute("""CREATE TRIGGER fail_lens BEFORE INSERT ON csv_import_rows WHEN NEW.kind='lens'
                      BEGIN SELECT RAISE(ABORT,'test write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='test write failure'):
        import_stats_csv(database, source)
    assert import_rows(database) == {'csv_imports': [], 'csv_import_rows': []}


def test_api_rejects_unauthorized_invalid_and_busy_requests(database, tmp_path):
    app = create_app(database)
    client = authorized_client(app)
    source = csv_file(tmp_path, [['카메라', '장비', 1, 100, '', '']])
    body = {'path': str(source)}
    assert client.post('/api/csv-imports', json=body, headers={'X-LensTracker-Token': 'bad'}).status_code == 403
    assert client.post('/api/csv-imports', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    for invalid in ({}, [], {'path': ''}, {'path': 123}, {'path': str(tmp_path / 'missing.csv')},
                    {'path': str(tmp_path)}, {'path': str(database)}):
        assert client.post('/api/csv-imports', json=invalid).status_code == 400
    for status in ('scanning', 'cancelling'):
        app.extensions['scanner'].state['status'] = status
        assert client.post('/api/csv-imports', json=body).status_code == 409
    assert list_csv_imports(database) == []
    app.extensions['scanner'].state['status'] = 'idle'
    assert client.post('/api/csv-imports', json=body).status_code == 201
    assert client.post('/api/csv-imports', json=body).status_code == 200
    assert client.get('/api/csv-imports/9999').status_code == 404
    assert client.get('/api/csv-imports').headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('selection', [None, '', 'H:/Picture/lenstracker-stats.csv'])
def test_native_csv_picker_requires_token_and_only_returns_selection(database, selection):
    calls = []

    def choose():
        calls.append(True)
        return selection

    client = authorized_client(create_app(database, choose_csv=choose))
    assert client.post('/api/desktop/csv', headers={'X-LensTracker-Token': 'bad'}).status_code == 403
    assert calls == []
    response = client.post('/api/desktop/csv')
    assert response.status_code == 200
    assert response.get_json() == {'path': selection or None}
    assert calls == [True]
    assert list_csv_imports(database) == []
    web = authorized_client(create_app(database))
    assert web.post('/api/desktop/csv').status_code == 404


def test_maximum_values_and_zero_counts_are_valid(database, tmp_path):
    source = csv_file(tmp_path, [
        ['카메라', '최대', 1_000_000_000_000, 100, 100_000_000_000, 0.01],
        ['카메라', '0도 보존', 0, 0, 0, 0],
    ])
    parsed = read_stats_csv(source)
    assert parsed['total_photos'] == 1_000_000_000_000
    assert parsed['rows'][1]['count'] == parsed['rows'][1]['cost'] == 0
