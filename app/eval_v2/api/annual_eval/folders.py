"""
app/eval_v2/api/annual_eval/folders.py
Annual Eval Folders API — 평가 폴더 상태 확인 및 일괄 생성
"""
import logging
from flask import request
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.utils.response import success, error
from app.services.firebase_service import get_firestore_client
from ._helpers import require_xhr, _NT_COLLECTIONS, _EVAL_FOLDER_COLLECTIONS, _EMP_ID_RE

logger = logging.getLogger(__name__)


@eval_v2_api.route('/annual-eval/folder-status', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_folder_status():
    """
    모든 교사의 평가 폴더 보유 여부 확인.
    NT 캐시의 eval_folder_url 유무로 판별.
    반환: {has_folder: [{emp_id, name, campus}], no_folder: [{emp_id, name, campus}]}
    """
    try:
        from app.services.nt_cache_service import get_nt_record

        db = get_firestore_client()
        has_folder = []
        no_folder  = []
        _seen = set()  # 중복 사번 skip — 우선순위 상위 컬렉션 값 유지

        # R&D/SIS 팀은 평가 폴더 대상 아님 — _EVAL_FOLDER_COLLECTIONS 만 조회.
        # _EVAL_FOLDER_COLLECTIONS 는 priority 순 — 첫 hit 가 primary.
        for col in _EVAL_FOLDER_COLLECTIONS:
            for doc in db.collection(col).stream():
                emp_id = doc.id.strip()
                if not emp_id or not _EMP_ID_RE.match(emp_id):
                    continue
                key = emp_id.lower()
                if key in _seen:
                    continue
                _seen.add(key)
                d = doc.to_dict()
                name   = d.get('name', '')
                campus = d.get('campus', '')

                nt_rec = get_nt_record(emp_id)
                folder_url = nt_rec.get('eval_folder_url', '')

                entry = {'emp_id': emp_id, 'name': name, 'campus': campus}
                if folder_url:
                    entry['folder_url'] = folder_url
                    has_folder.append(entry)
                else:
                    no_folder.append(entry)

        no_folder.sort(key=lambda x: x.get('name', ''))
        has_folder.sort(key=lambda x: x.get('name', ''))

        return success({'has_folder': has_folder, 'no_folder': no_folder})
    except Exception:
        logger.exception('api_annual_eval_folder_status error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/create-folders', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_create_folders():
    """
    폴더가 없는 교사들의 평가 폴더를 일괄 생성.
    요청: {emp_ids: [str]}  — 생성 대상 사번 목록
    Drive API로 폴더 생성 → NT 캐시 & NT Info 시트에 URL 저장.
    """
    try:
        from app.services.drive_service import get_or_create_eval_folder, preload_bv_url_map
        from app.services.nt_cache_service import get_nt_record, update_nt_record_field

        bv_url_map = preload_bv_url_map()

        data    = request.get_json(silent=True) or {}
        emp_ids = data.get('emp_ids', [])
        if not isinstance(emp_ids, list) or not emp_ids:
            return error('emp_ids list required.', 400)
        if len(emp_ids) > 200:
            return error('Too many targets (max 200).', 400)

        results         = []
        created         = 0
        existed         = 0
        bv_write_failed = []  # BV열 기록 실패 사번 (Drive 폴더는 생성됐으나 시트에 URL 누락)

        for eid in emp_ids:
            eid = str(eid).strip()
            if not eid or not _EMP_ID_RE.match(eid):
                results.append({'emp_id': eid, 'status': 'SKIPPED', 'message': 'Invalid emp_id'})
                continue
            try:
                nt_rec = get_nt_record(eid)
                name   = nt_rec.get('name', '')

                folder_info = get_or_create_eval_folder(eid.upper(), name, bv_url_map=bv_url_map)
                folder_url  = folder_info['folder_url']

                update_nt_record_field(eid, 'eval_folder_url', folder_url)

                if folder_info['created']:
                    created += 1
                else:
                    existed += 1

                # BV 쓰기 실패 추적 — False 만 실패. None 은 이미 존재하여 스킵된 케이스.
                bv_written = folder_info.get('bv_written')
                if bv_written is False:
                    bv_write_failed.append(eid)

                results.append({
                    'emp_id':     eid,
                    'status':     'CREATED' if folder_info['created'] else 'EXISTS',
                    'folder_url': folder_url,
                    'bv_written': bv_written,
                })
            except Exception as e:
                logger.exception('create-folders error [%s]: %s', eid, e)
                results.append({'emp_id': eid, 'status': 'ERROR', 'message': str(e)[:100]})

        return success({
            'created': created,
            'existed': existed,
            'total': len(emp_ids),
            'results': results,
            'bv_write_failed': bv_write_failed,
            'bv_write_failed_count': len(bv_write_failed),
        })
    except Exception:
        logger.exception('api_annual_eval_create_folders error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/bv-audit', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_bv_audit():
    """
    NT Info 시트 BV열 ↔ Firestore nt_*.eval_folder_url 간 divergence 감사.

    반환:
      {
        total_active: int,
        matches: int,                    # 양쪽 URL 동일
        missing_in_sheet: [              # Firestore 에는 있는데 시트 BV 비어있음
          {emp_id, name, campus, firestore_url}
        ],
        missing_in_firestore: [          # 시트 BV 에는 있는데 Firestore 비어있음
          {emp_id, sheet, sheet_url}
        ],
        divergent: [                     # 양쪽 모두 있으나 URL 불일치
          {emp_id, name, campus, firestore_url, sheet_url}
        ],
        both_missing_count: int,         # 양쪽 다 비어있음 (정상 — 아직 미생성 교사)
      }
    """
    try:
        from app.services.drive_service import preload_bv_url_map

        db = get_firestore_client()
        bv_map = preload_bv_url_map()  # {emp_id_lower: sheet_url}

        # Firestore 활성 컬렉션 수집 (평가 폴더 대상만 — R&D/SIS 제외).
        # _EVAL_FOLDER_COLLECTIONS 는 priority 순 튜플 — 첫 hit 유지로 우선순위 적용.
        fs_map = {}   # {emp_id_lower: {emp_id, name, campus, firestore_url}}
        for col in _EVAL_FOLDER_COLLECTIONS:
            for doc in db.collection(col).stream():
                emp_id = doc.id.strip()
                if not emp_id or not _EMP_ID_RE.match(emp_id):
                    continue
                key = emp_id.lower()
                if key in fs_map:
                    continue  # 우선순위 상위 컬렉션 값 유지
                d = doc.to_dict() or {}
                fs_map[key] = {
                    'emp_id':        emp_id,
                    'name':          d.get('name', ''),
                    'campus':        d.get('campus', ''),
                    'firestore_url': (d.get('eval_folder_url') or '').strip(),
                }

        # nt_retire 수집 — missing_in_firestore 분류에 사용 (퇴직자 vs 진짜 orphan)
        retire_map = {}  # {emp_id_lower: {name, retire_date}}
        try:
            for doc in db.collection('nt_retire').stream():
                rid = doc.id.strip()
                if not rid:
                    continue
                rd = doc.to_dict() or {}
                retire_map[rid.lower()] = {
                    'name':        rd.get('name', ''),
                    'retire_date': rd.get('retire_date', ''),
                }
        except Exception:
            logger.exception('bv-audit: nt_retire scan failed — missing_in_firestore 분류 실패 가능')

        all_keys = set(fs_map.keys()) | set(bv_map.keys())

        matches = 0
        missing_in_sheet = []
        missing_in_firestore = []  # 각 entry 에 classification: 'retired' | 'orphan'
        divergent = []
        both_missing = 0

        for key in all_keys:
            fs_rec = fs_map.get(key)
            fs_url = (fs_rec or {}).get('firestore_url', '')
            sheet_url = (bv_map.get(key) or '').strip()

            if not fs_url and not sheet_url:
                both_missing += 1
                continue

            if fs_url and sheet_url:
                if fs_url == sheet_url:
                    matches += 1
                else:
                    divergent.append({
                        'emp_id':        fs_rec['emp_id'] if fs_rec else key.upper(),
                        'name':          (fs_rec or {}).get('name', ''),
                        'campus':        (fs_rec or {}).get('campus', ''),
                        'firestore_url': fs_url,
                        'sheet_url':     sheet_url,
                    })
                continue

            if fs_url and not sheet_url:
                missing_in_sheet.append({
                    'emp_id':        fs_rec['emp_id'] if fs_rec else key.upper(),
                    'name':          (fs_rec or {}).get('name', ''),
                    'campus':        (fs_rec or {}).get('campus', ''),
                    'firestore_url': fs_url,
                })
                continue

            if sheet_url and not fs_url:
                # nt_retire 에 있으면 '퇴직자' — 정상 상태 (BV에 URL 이 남아있는 것이 설계상 자연스러움)
                # 아니면 '진짜 orphan' — 활성/퇴직 어느 쪽에도 없는 emp_id. 시트 오염 또는 수동 정리 필요.
                retire_rec = retire_map.get(key)
                entry = {
                    'emp_id':    key.upper(),
                    'sheet_url': sheet_url,
                }
                if retire_rec:
                    entry['classification'] = 'retired'
                    entry['name']        = retire_rec.get('name', '')
                    entry['retire_date'] = retire_rec.get('retire_date', '')
                else:
                    entry['classification'] = 'orphan'
                missing_in_firestore.append(entry)

        # 정렬 (이름 기준 → emp_id)
        missing_in_sheet.sort(key=lambda x: (x.get('name') or '', x['emp_id']))
        missing_in_firestore.sort(key=lambda x: (x.get('classification') or '', x.get('name') or '', x['emp_id']))
        divergent.sort(key=lambda x: (x.get('name') or '', x['emp_id']))

        retired_count = sum(1 for x in missing_in_firestore if x.get('classification') == 'retired')
        orphan_count  = sum(1 for x in missing_in_firestore if x.get('classification') == 'orphan')

        return success({
            'total_active':          len(fs_map),
            'matches':               matches,
            'missing_in_sheet':      missing_in_sheet,
            'missing_in_sheet_count': len(missing_in_sheet),
            'missing_in_firestore':  missing_in_firestore,
            'missing_in_firestore_count': len(missing_in_firestore),
            'missing_retired_count': retired_count,
            'missing_orphan_count':  orphan_count,
            'divergent':             divergent,
            'divergent_count':       len(divergent),
            'both_missing_count':    both_missing,
        })
    except Exception:
        logger.exception('api_annual_eval_bv_audit error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/duplicate-audit', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_duplicate_audit():
    """동일 emp_id 가 여러 활성 nt_* 컬렉션에 중복 존재하는지 감사.
    반환:
      {
        duplicates: [
          {
            emp_id, name,
            primary: 'nt_dyb',            # 우선순위 최상위 컬렉션
            secondary: ['nt_creo_*', ...],# 나머지
            occurrences: [{collection, name, campus, weight}, ...] # priority 순
          }, ...
        ],
        count
      }
    """
    try:
        from app.services.firebase_service import (
            NT_COLLECTIONS_BY_PRIORITY, NT_COLLECTION_PRIORITY,
        )

        db = get_firestore_client()
        occurrences_by_emp = {}  # {emp_id_lower: [{...}, ...]}
        for col in NT_COLLECTIONS_BY_PRIORITY:
            for doc in db.collection(col).stream():
                emp_id = doc.id.strip()
                if not emp_id or not _EMP_ID_RE.match(emp_id):
                    continue
                d = doc.to_dict() or {}
                occurrences_by_emp.setdefault(emp_id.lower(), []).append({
                    'emp_id':     emp_id,
                    'collection': col,
                    'name':       d.get('name', ''),
                    'campus':     d.get('campus', ''),
                    'weight':     NT_COLLECTION_PRIORITY.get(col, 999),
                })

        duplicates = []
        for occs in occurrences_by_emp.values():
            if len(occs) < 2:
                continue
            occs_sorted = sorted(occs, key=lambda o: o['weight'])
            primary = occs_sorted[0]
            duplicates.append({
                'emp_id':      primary['emp_id'],
                'name':        primary['name'],
                'primary':     primary['collection'],
                'secondary':   [o['collection'] for o in occs_sorted[1:]],
                'occurrences': occs_sorted,
            })

        duplicates.sort(key=lambda x: (x.get('name') or '', x['emp_id']))

        return success({'duplicates': duplicates, 'count': len(duplicates)})
    except Exception:
        logger.exception('api_annual_eval_duplicate_audit error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/bv-fix', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_bv_fix():
    """
    bv-audit 결과 중 missing_in_sheet (Firestore URL → 시트 BV 복사) 건들을 일괄 수정.
    요청: {emp_ids: [str]}
    """
    try:
        from app.services.drive_service import save_folder_url_to_nt_info
        from app.services.nt_cache_service import get_nt_record

        data = request.get_json(silent=True) or {}
        emp_ids = data.get('emp_ids', [])
        if not isinstance(emp_ids, list) or not emp_ids:
            return error('emp_ids list required.', 400)
        if len(emp_ids) > 200:
            return error('Too many targets (max 200).', 400)

        fixed = []
        failed = []
        skipped = []

        for eid in emp_ids:
            eid = str(eid).strip()
            if not eid or not _EMP_ID_RE.match(eid):
                skipped.append({'emp_id': eid, 'reason': 'invalid_emp_id'})
                continue
            try:
                nt_rec = get_nt_record(eid) or {}
                fs_url = (nt_rec.get('eval_folder_url') or '').strip()
                if not fs_url:
                    skipped.append({'emp_id': eid, 'reason': 'firestore_url_empty'})
                    continue
                ok = save_folder_url_to_nt_info(eid, fs_url)
                if ok:
                    fixed.append({'emp_id': eid, 'url': fs_url})
                else:
                    failed.append({'emp_id': eid, 'reason': 'save_returned_false'})
            except Exception as e:
                logger.exception('bv-fix error [%s]', eid)
                failed.append({'emp_id': eid, 'reason': str(e)[:100]})

        return success({
            'fixed_count':   len(fixed),
            'fixed':         fixed,
            'failed_count':  len(failed),
            'failed':        failed,
            'skipped_count': len(skipped),
            'skipped':       skipped,
        })
    except Exception:
        logger.exception('api_annual_eval_bv_fix error')
        return error('An internal error occurred.', 500)
