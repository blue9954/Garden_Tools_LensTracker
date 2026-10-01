"""Conservative lens identity and transactional consolidation of EXIF aliases."""
import re
import unicodedata


def lens_key(model):
    text = unicodedata.normalize('NFKC', model).casefold().strip()
    text = re.sub(r'^canon\s+(?=(?:ef|rf)\s*\d)', '', text)
    text = re.sub(r'^fujifilm\s+(?=(?:xf|xc|gf)\s*\d)', '', text)
    # Sigma Art EXIF identifiers include both manufacturer and maker-note forms.
    if re.fullmatch(r'(?:sigma\s+)?\d+mm\s+f/?[\d.]+\s+dg\s+hsm(?:\s+a\d{0,3}|\s*\|\s*art\s*\d{0,3})', text):
        text = re.sub(r'^sigma\s+', '', text)
        text = re.sub(r'(?:\s+a\d{0,3}|\s*\|\s*art\s*\d{0,3})$', ' art', text)
    return re.sub(r'\s+|(?<=f)/', '', text)


DISPLAY_NAMES = {lens_key(name): name for name in (
    'Canon EF 24mm f/1.4L II USM',
    'Canon EF 50mm f/1.2L USM',
    'Canon EF 24-70mm f/2.8L II USM',
    'SIGMA 135mm F1.8 DG HSM A017',
    'TAMRON SP 35mm F1.4 Di USD F045',
)}


def merge_lenses(db):
    groups = {}
    for row in db.execute("SELECT * FROM equipment WHERE kind='lens' ORDER BY id"):
        groups.setdefault(lens_key(row['model']), []).append(dict(row))
    changes = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        keeper, *duplicates = items
        # Preserve a user-edited display name in preference to generated names.
        name = next((r['name'] for r in items if r['name'] != r['model']),
                    DISPLAY_NAMES.get(key, keeper['name']))
        notes = list(dict.fromkeys(line for r in items for line in r['note'].splitlines() if line))
        prices = {}
        for field, label in [('new_price', '기준 가격'), ('purchase_price', '구입 가격')]:
            values = [r[field] for r in items if r[field] is not None]
            prices[field] = values[0] if values else None
            for r in items:
                if r[field] is not None and r[field] != prices[field]:
                    notes.append(f"통합 전 {r['model']} {label}: {r[field]:,}원")
        aliases = list(dict.fromkeys(r['model'] for r in items))
        notes.append('통합된 렌즈 표기: ' + ' / '.join(aliases))
        for row in duplicates:
            db.execute('UPDATE photos SET lens_id=? WHERE lens_id=?', (keeper['id'], row['id']))
            db.execute('DELETE FROM equipment WHERE id=?', (row['id'],))
        db.execute('UPDATE equipment SET name=?,new_price=?,purchase_price=?,note=? WHERE id=?',
                   (name, prices['new_price'], prices['purchase_price'], '\n'.join(dict.fromkeys(notes)), keeper['id']))
        changes.append({'id': keeper['id'], 'name': name, 'merged_ids': [r['id'] for r in duplicates]})
    return changes
