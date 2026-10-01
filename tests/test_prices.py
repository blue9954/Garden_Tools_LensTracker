import pytest

from lenstracker.database import connect, equipment_id, initialize
from lenstracker.prices import apply_prices, catalog, reference_price


@pytest.mark.parametrize('model,expected', [
    ('Canon EF 24mm f/1.4 L II USM', 1200000),
    ('Canon EF 24-70mm f/2.8L II USM', 1800000),
    ('RF70-200mm F2.8 L IS USM', 3400000),
    ('GF32-64mmF4 R LM WR', 3000000),
    ('SIGMA 135mm F1.8 DG HSM A017', 1500000),
    ('135mm F1.8 DG HSM | Art 017', 1500000),
    ('TAMRON SP 45mm F/1.8 Di VC USD F013', 650000),
])
def test_exif_variants(model, expected):
    assert reference_price(model)[0] == expected


@pytest.mark.parametrize('model', [
    'TAMRON SP 35mm F1.4 Di USD F045', 'RF50mm F1.8 STM',
    'XF56mmF1.2 R', 'AF 27/2.8', 'MJ56mm F1.8X DA DSM',
])
def test_distinct_or_ambiguous_models_are_not_priced(model):
    assert reference_price(model) is None


def test_catalog_and_existing_prices(tmp_path):
    assert len(catalog()) == 46
    path = tmp_path / 'library.sqlite'
    initialize(path)
    with connect(path) as db:
        lens = equipment_id(db, 'lens', 'Canon EF 24mm f/1.4L II USM')
        assert db.execute('SELECT new_price FROM equipment WHERE id=?', (lens,)).fetchone()[0] == 1200000
        db.execute('UPDATE equipment SET new_price=999,purchase_price=123,note=? WHERE id=?', ('내 메모', lens))
    initialize(path)
    with connect(path) as db:
        assert db.execute('SELECT new_price FROM equipment WHERE id=?', (lens,)).fetchone()[0] == 999
        assert len(apply_prices(db, overwrite=True)) == 1
        apply_prices(db, overwrite=True)
        row = db.execute('SELECT * FROM equipment WHERE id=?', (lens,)).fetchone()
        assert row['new_price'] == 1200000
        assert row['purchase_price'] == 123
        assert row['note'].startswith('내 메모\n')
        assert row['note'].count('렌즈가격.json:') == 1
        assert '800,000 ~ 1,200,000원' in row['note']
        assert equipment_id(db, 'lens', row['model']) == lens
        camera = equipment_id(db, 'camera', row['model'])
        assert db.execute('SELECT new_price FROM equipment WHERE id=?', (camera,)).fetchone()[0] is None
