import pytest

from lenstracker.analytics import report
from lenstracker.database import connect, equipment_id, initialize
from lenstracker.lenses import lens_key, merge_lenses


@pytest.mark.parametrize('a,b', [
    ('EF24mm f/1.4L II USM', 'Canon EF 24mm f/1.4 L Ⅱ USM'),
    ('SIGMA 135mm F1.8 DG HSM A017', '135mm F1.8 DG HSM | Art 017'),
    ('TAMRON SP 35mm F1.4 Di USD F045', 'TAMRON SP 35mm F/1.4 Di USD F045'),
])
def test_aliases(a, b):
    assert lens_key(a) == lens_key(b)


@pytest.mark.parametrize('a,b', [
    ('Canon EF 24mm f/1.4L USM', 'Canon EF 24mm f/1.4L II USM'),
    ('RF50mm F1.2 L USM', 'RF50mm F1.8 STM'),
    ('SIGMA 35mm F1.4 DG HSM A', 'TAMRON 35mm F1.4 DG HSM A'),
    ('XF56mmF1.2 R', 'XF56mmF1.2 R WR'),
    ('AF 27/2.8', 'TTARTISAN AF 27mm F2.8'),
])
def test_distinct_models(a, b):
    assert lens_key(a) != lens_key(b)


def test_merge_preserves_records_and_combines_statistics(tmp_path):
    path = tmp_path / 'library.sqlite'
    initialize(path)
    with connect(path) as db:
        camera = equipment_id(db, 'camera', 'Camera')
        db.execute('UPDATE equipment SET new_price=1000 WHERE id=?', (camera,))
        ids = []
        for model, price, purchase, note in [
            ('EF24mm f/1.4L II USM', None, 300, '메모 A'),
            ('Canon EF 24mm f/1.4L II USM', 1200000, 400, '메모 B'),
            ('Canon EF 24mm f/1.4 L II USM', 1300000, None, '메모 A'),
        ]:
            ids.append(db.execute("INSERT INTO equipment(kind,model,name,new_price,purchase_price,note) VALUES('lens',?,?,?,?,?)",
                                  (model, model, price, purchase, note)).lastrowid)
        for i, lens in enumerate(ids):
            photo = db.execute("INSERT INTO photos(digest,camera_id,lens_id,taken_at,format) VALUES(?,?,?,?, 'JPEG')",
                               (str(i), camera, lens, '2026-09-01')).lastrowid
            db.execute('INSERT INTO files VALUES(?,?,?,?)', (f'{i}.jpg', 42, 1, photo))
        assert len(merge_lenses(db)) == 1
        assert merge_lenses(db) == []
        row = db.execute('SELECT * FROM equipment WHERE id=?', (ids[0],)).fetchone()
        assert row['name'] == 'Canon EF 24mm f/1.4L II USM'
        assert row['new_price'] == 1200000 and row['purchase_price'] == 300
        assert all(text in row['note'] for text in ['메모 A', '메모 B', '1,300,000', '400'])
        assert db.execute('SELECT count(*) FROM files').fetchone()[0] == 3
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        for model in ['Canon EF 24mm f/1.4 L II USM', 'EF24mm f/1.4L II USM']:
            assert equipment_id(db, 'lens', model) == ids[0]
    initialize(path)
    result = report(path, {})
    assert result['summary']['total'] == 3
    assert result['summary']['lenses'] == 1
    assert result['summary']['investment'] == 1201000
    assert result['rankings']['lens'][0]['count'] == 3
    assert result['rankings']['lens'][0]['cost'] == 400000
    assert len(result['combinations']) == 1
    assert result['combinations'][0]['count'] == 3
    assert report(path, {'lens': str(ids[0])})['summary']['total'] == 3
