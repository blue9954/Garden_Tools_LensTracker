"""User-supplied lens reference prices (KRW, upper end of each range)."""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def model_key(model):
    text = unicodedata.normalize('NFKC', model).casefold().strip()
    text = re.sub(r'^(canon|fujifilm|tamron|sigma)\s+', '', text)
    text = re.sub(r'^a\s+(?=\d)', '', text)
    text = re.sub(r'\s*\|\s*art(?:\s+\d{3})?$', '', text)
    text = re.sub(r'\s+(?:a\d{3}|a|f\d{3})$', '', text)
    # EXIF may omit mm or put a slash between F and the aperture.
    return re.sub(r'\s+|mm|/', '', text)


@lru_cache(maxsize=1)
def catalog():
    source = Path(__file__).with_name('lens_prices.json')
    result = {}
    for model, raw in json.loads(source.read_text(encoding='utf-8-sig')).items():
        amounts = [int(value.strip().replace(',', '')) for value in raw.split('~')]
        if not 1 <= len(amounts) <= 2 or min(amounts) < 0:
            raise ValueError(f'Invalid reference price: {model}')
        key = model_key(model)
        if key in result:
            raise ValueError(f'Ambiguous reference model: {model}')
        result[key] = (max(amounts), f'렌즈가격.json: {raw}원' +
                       (' · 범위 최고가 적용' if len(amounts) == 2 else ''))
    return result


def reference_price(model):
    return catalog().get(model_key(model))


def apply_prices(db, overwrite=False):
    changed = []
    for row in db.execute("SELECT id,model,name,new_price,note FROM equipment WHERE kind='lens'").fetchall():
        price = reference_price(row['model']) or reference_price(row['name'])
        if price is None or (row['new_price'] is not None and not overwrite):
            continue
        amount, source = price
        note = '\n'.join(line for line in row['note'].splitlines()
                         if not line.startswith('렌즈가격.json:'))
        note = '\n'.join(filter(None, (note, source)))
        db.execute('UPDATE equipment SET new_price=?,note=? WHERE id=?',
                   (amount, note, row['id']))
        changed.append({'id': row['id'], 'model': row['model'],
                        'previous_price': row['new_price'], 'price': amount})
    return changed
