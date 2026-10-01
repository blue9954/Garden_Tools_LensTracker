from datetime import date

from .database import connect


def filters(args):
    conditions, params = [], {}
    for key, op in [('start', '>='), ('end', '<=')]:
        value = args.get(key)
        if value:
            try:
                date.fromisoformat(value)
            except (ValueError, TypeError):
                raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
            conditions.append(f'substr(p.taken_at,1,10) {op} :{key}')
            params[key] = value
    if params.get('start', '') > params.get('end', '9999-12-31'):
        raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
    for key in ('camera', 'lens'):
        value = args.get(key)
        if value:
            if value == 'unknown':
                conditions.append(f'p.{key}_id IS NULL')
            else:
                try:
                    params[key] = int(value)
                except (ValueError, TypeError):
                    raise ValueError('장비 필터가 올바르지 않습니다.')
                conditions.append(f'p.{key}_id=:{key}')
    return (' WHERE ' + ' AND '.join(conditions) if conditions else ''), params


def rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params)]


def report(database, args):
    where, params = filters(args)
    cte = 'WITH selected AS (SELECT p.* FROM photos p' + where + ') '
    with connect(database) as db:
        total_library = db.execute('SELECT count(*) FROM photos').fetchone()[0]
        summary = dict(db.execute(cte + '''SELECT count(*) AS total,
            count(DISTINCT camera_id) AS cameras, count(DISTINCT lens_id) AS lenses,
            coalesce(sum(camera_id IS NULL),0) AS missing_camera,
            coalesce(sum(lens_id IS NULL),0) AS missing_lens,
            coalesce(sum(taken_at IS NULL),0) AS missing_date FROM selected''', params).fetchone())
        summary['library_total'] = total_library
        summary['registered_files'] = db.execute('SELECT count(*) FROM files').fetchone()[0]
        summary['investment'] = db.execute('SELECT sum(new_price) FROM equipment').fetchone()[0]
        summary['priced_equipment'] = db.execute('SELECT count(*) FROM equipment WHERE new_price IS NOT NULL').fetchone()[0]
        equipment = rows(db, '''SELECT e.*, count(p.id) AS count FROM equipment e LEFT JOIN photos p
            ON (e.kind='camera' AND p.camera_id=e.id) OR (e.kind='lens' AND p.lens_id=e.id)
            GROUP BY e.id ORDER BY e.kind,e.name COLLATE NOCASE''')
        for item in equipment:
            item['cost'] = item['new_price'] / item['count'] if item['count'] and item['new_price'] is not None else None
            item['purchase_cost'] = item['purchase_price'] / item['count'] if item['count'] and item['purchase_price'] is not None else None
        rankings = {}
        for kind in ('camera', 'lens'):
            rankings[kind] = rows(db, cte + f'''SELECT e.id, coalesce(e.name,'미상') AS name,
                e.new_price, e.purchase_price, count(*) AS count FROM selected p
                LEFT JOIN equipment e ON e.id=p.{kind}_id
                GROUP BY p.{kind}_id ORDER BY count DESC,name''', params)
            for item in rankings[kind]:
                item['cost'] = item['new_price'] / item['count'] if item['new_price'] is not None else None
                item['share'] = item['count'] / summary['total'] if summary['total'] else 0
        combinations = rows(db, cte + '''SELECT c.id AS camera_id,l.id AS lens_id,
            coalesce(c.name,'카메라 미상') AS camera, coalesce(l.name,'렌즈 미상') AS lens,
            c.new_price+l.new_price AS price, count(*) AS count
            FROM selected p LEFT JOIN equipment c ON c.id=p.camera_id
            LEFT JOIN equipment l ON l.id=p.lens_id GROUP BY p.camera_id,p.lens_id
            ORDER BY count DESC,camera,lens''', params)
        for item in combinations:
            item['name'] = item['camera'] + ' + ' + item['lens']
            item['cost'] = item['price'] / item['count'] if item['price'] is not None else None
            item['share'] = item['count'] / summary['total'] if summary['total'] else 0
        monthly = rows(db, cte + '''SELECT substr(taken_at,1,7) AS month,count(*) AS count
            FROM selected WHERE taken_at IS NOT NULL GROUP BY month ORDER BY month''', params)
        focal = rows(db, cte + '''SELECT CASE WHEN focal IS NULL THEN '미상'
            WHEN focal < 24 THEN '24mm 미만' WHEN focal < 35 THEN '24–34mm'
            WHEN focal < 50 THEN '35–49mm' WHEN focal < 85 THEN '50–84mm'
            WHEN focal < 135 THEN '85–134mm' ELSE '135mm 이상' END AS name,
            count(*) AS count, min(coalesce(focal,999999)) AS sort FROM selected
            GROUP BY name ORDER BY sort''', params)
        allocated = dict(db.execute(cte + ''', camera_counts AS (
            SELECT camera_id,count(*) n FROM photos GROUP BY camera_id), lens_counts AS (
            SELECT lens_id,count(*) n FROM photos GROUP BY lens_id)
            SELECT count(*) AS covered,avg(1.0*c.new_price/cc.n+1.0*l.new_price/lc.n) AS average_cost
            FROM selected p JOIN equipment c ON c.id=p.camera_id JOIN equipment l ON l.id=p.lens_id
            JOIN camera_counts cc ON cc.camera_id=p.camera_id JOIN lens_counts lc ON lc.lens_id=p.lens_id
            WHERE c.new_price IS NOT NULL AND l.new_price IS NOT NULL''', params).fetchone())
        summary.update(allocated)
        summary['coverage'] = summary['covered'] / summary['total'] if summary['total'] else 0
    return dict(summary=summary, equipment=equipment, rankings=rankings,
                combinations=combinations, monthly=monthly, focal=focal)


def photo_page(database, args):
    where, params = filters(args)
    try:
        page = max(1, int(args.get('page', 1)))
    except (ValueError, TypeError):
        raise ValueError('페이지 번호가 올바르지 않습니다.')
    with connect(database) as db:
        total = db.execute('SELECT count(*) FROM photos p' + where, params).fetchone()[0]
        page = min(page, max(1, (total + 49) // 50))
        params['offset'] = (page - 1) * 50
        photos = rows(db, '''SELECT p.*,c.name AS camera,l.name AS lens,
            (SELECT min(path) FROM files f WHERE f.photo_id=p.id) AS path
            FROM photos p LEFT JOIN equipment c ON c.id=p.camera_id
            LEFT JOIN equipment l ON l.id=p.lens_id''' + where +
            ' ORDER BY p.taken_at DESC,p.id DESC LIMIT 50 OFFSET :offset', params)
    return dict(photos=photos, page=page, total=total, pages=max(1, (total + 49) // 50))
