import logging
import os
import json
import firebase_admin
from firebase_admin import credentials, auth, firestore

logger = logging.getLogger(__name__)

# NT 시트 → Firestore 컬렉션 매핑. 캠퍼스 추가 시 이 한 곳만 수정.
# sync_nt_to_firestore / fetch_nt_from_firestore / fetch_nickname_from_firestore /
# sync_retire_to_firestore 공용. 기존 하드코딩 drift 방지.
NT_SHEET_TO_COLLECTION = {
    'dyb':          'nt_dyb',
    'sub':          'nt_sub',
    'R&D_SIS':      'nt_rnd',
    'Brand X':  'nt_brand_x',
    'Brand Y':  'nt_brand_y',
    'Brand Z':   'nt_brand_z',
}

# 중복 사번 해결 우선순위 — 동일 emp_id 가 여러 시트에 존재할 때 어느 값을
# authoritative 로 쓸지 결정. 값이 작을수록 높은 우선순위. 동률 허용.
# - CREO 3종은 동일 weight(2): 한 사번이 CREO 2~3곳에 동시 존재 가능성은 거의 없음.
# - R&D_SIS(nt_rnd) 는 타 시트와 겹치지 않으므로 해결 대상이 아님 → max weight(9).
NT_COLLECTION_PRIORITY = {
    'nt_dyb':          0,
    'nt_sub':          1,
    'nt_brand_x':  2,
    'nt_brand_y':  2,
    'nt_brand_z':   2,
    'nt_rnd':          9,
}

# 우선순위 순(낮은 weight 우선)으로 정렬된 컬렉션 튜플. 순회 시 첫 hit 가 primary.
# 동률 내부 순서는 sorted 의 stable 보장(선언 순서) 활용.
NT_COLLECTIONS_BY_PRIORITY = tuple(
    sorted(NT_COLLECTION_PRIORITY, key=lambda c: NT_COLLECTION_PRIORITY[c])
)
# = ('nt_dyb', 'nt_sub', 'nt_brand_x', 'nt_brand_y', 'nt_brand_z', 'nt_rnd')

# 하위 호환 alias — 기존 코드가 ACTIVE_NT_COLLECTIONS 를 import 하여 사용.
# 이제 priority 순으로 정렬된 튜플과 동일.
ACTIVE_NT_COLLECTIONS = NT_COLLECTIONS_BY_PRIORITY


def dedupe_records_by_priority(records: list) -> list:
    """동일 emp_id 가 여러 컬렉션에 있을 때 우선순위가 가장 높은 하나만 유지.
    각 record 는 '_collection' 키를 포함해야 함 — 호출자가 stream() 돌리며 주입.
    동률이면 리스트에서 먼저 만난 쪽 유지.
    반환값에서도 '_collection' 키는 제거하지 않음 — 호출자가 필요 시 pop.
    """
    seen = {}   # {emp_id_lower: (weight, record)}
    for r in records:
        eid = str(r.get('emp_id') or '').strip().lower()
        if not eid:
            continue
        col = r.get('_collection', '')
        w = NT_COLLECTION_PRIORITY.get(col, 999)
        prev = seen.get(eid)
        if prev is None or w < prev[0]:
            seen[eid] = (w, r)
    return [v[1] for v in seen.values()]

def initialize_firebase():
    if firebase_admin._apps:
        return
    key_info = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    key_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if key_info:
        cred = credentials.Certificate(json.loads(key_info))
    elif key_path:
        cred = credentials.Certificate(key_path)
    else:
        raise RuntimeError(
            'Firebase credentials not set. '
            'Set FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT environment variable.'
        )
    bucket_name = os.environ.get('FIREBASE_STORAGE_BUCKET')
    options = {'storageBucket': bucket_name} if bucket_name else None
    if not bucket_name:
        logger.warning('FIREBASE_STORAGE_BUCKET not set — Storage features disabled.')
    firebase_admin.initialize_app(cred, options)


def get_storage_bucket():
    """Firebase Storage 기본 버킷 반환. 미설정 시 None."""
    if not os.environ.get('FIREBASE_STORAGE_BUCKET'):
        return None
    try:
        from firebase_admin import storage
        return storage.bucket()
    except Exception:
        logger.exception('get_storage_bucket failed')
        return None

def verify_firebase_token(id_token):
    try:
        decoded = auth.verify_id_token(id_token, check_revoked=True)
        return decoded
    except Exception as e:
        logger.exception('Firebase token verification failed: %s', e)
        return None

def get_user_by_email(email):
    try:
        return auth.get_user_by_email(email)
    except Exception as e:
        logger.exception('Firebase get_user_by_email failed: %s', e)
        return None

def get_firestore_client():
    return firestore.client()

def _process_rehires_on_nt_sync(db, retire_snaps: dict) -> list:
    """NT sync 대상 emp_id 중 nt_retire 에도 있는 재입사자 처리.
    Drive 폴더를 previous_parent_folder_id 로 복원하고 nt_retire 문서 삭제.

    retire_snaps: {emp_id: DocumentSnapshot}
    반환: [{'emp_id', 'status', ...}]
      status: 'restored' | 'idempotent' | 'no_folder_meta' | 'drive_failed'
    """
    from app.services.drive_service import restore_folder_from_retired, extract_folder_id

    rehired = []
    for emp_id, snap in retire_snaps.items():
        data = snap.to_dict() or {}
        folder_url = (data.get('eval_folder_url') or '').strip()
        prev_parent = (data.get('previous_parent_folder_id') or '').strip()
        folder_id = extract_folder_id(folder_url) if folder_url else None

        # Legacy doc (아카이브 메타 없음) — nt_retire 만 제거, 수동 Drive 복원 필요.
        if not folder_id or not prev_parent:
            db.collection('nt_retire').document(emp_id).delete()
            rehired.append({'emp_id': emp_id, 'status': 'no_folder_meta'})
            logger.warning('rehire without folder metadata — nt_retire deleted, Drive restore skipped: %s', emp_id)
            continue

        result = restore_folder_from_retired(folder_id, prev_parent)
        if result['success']:
            db.collection('nt_retire').document(emp_id).delete()
            rehired.append({
                'emp_id': emp_id,
                'folder_id': folder_id,
                'new_parent_folder_id': prev_parent,
                'status': 'idempotent' if result.get('already_restored') else 'restored',
            })
        elif result.get('gone'):
            # Drive 폴더 자체가 삭제됨 (404) — nt_retire 만 정리해 무한 재시도 루프 차단
            db.collection('nt_retire').document(emp_id).delete()
            rehired.append({'emp_id': emp_id, 'folder_id': folder_id, 'status': 'folder_gone'})
            logger.error('rehire Drive folder gone — nt_retire deleted, manual folder re-creation may be needed: %s', emp_id)
        else:
            # Drive 일시 장애 — nt_retire 유지. NT sync 는 계속 진행 (활성 upsert 됨).
            # 중간 상태: 활성 + nt_retire 양쪽 존재. 다음 NT sync 에서 재시도.
            rehired.append({'emp_id': emp_id, 'status': 'drive_failed'})
            logger.warning('rehire Drive restore failed — nt_retire preserved, active upsert continues: %s', emp_id)

    return rehired


def sync_nt_to_firestore(sheet_name, records, actor='unknown'):
    """Sheets에서 읽어온 NT 데이터를 Firestore에 저장.

    Rehire 처리: NT Info 시트에 있는 emp_id 중 nt_retire 에도 있는 사람은 재입사로 간주
    → Drive 폴더 복원 + nt_retire 삭제 → 활성 컬렉션에 upsert.

    Retire 시트는 누적 이력 (HR 이 삭제하지 않음). 재직/퇴사 판단의 권위는 NT Info 시트
    (즉 활성 컬렉션). sync 순서 무관 — NT sync 한 번으로 재입사 완결.
    """
    try:
        db = get_firestore_client()
        collection_name = NT_SHEET_TO_COLLECTION.get(sheet_name)
        if not collection_name:
            return False, f'Unknown sheet: {sheet_name}'

        valid_records = [r for r in records if r.get('emp_id', '').strip()]
        emp_ids = [r['emp_id'].strip() for r in valid_records]

        # nt_retire 배치 조회 — 이번 sync 대상 중 재입사자 후보 식별
        rehired = []
        if emp_ids:
            retire_refs = [db.collection('nt_retire').document(eid) for eid in emp_ids]
            retire_snaps = {snap.id: snap for snap in db.get_all(retire_refs) if snap.exists}
            if retire_snaps:
                try:
                    rehired = _process_rehires_on_nt_sync(db, retire_snaps)
                except Exception:
                    logger.exception('rehire processing failed — NT upsert continues')
                    rehired = []

        # Firestore batch 제한(500건) 대비 400건 단위 chunk 처리
        CHUNK = 400
        for i in range(0, len(valid_records), CHUNK):
            batch = db.batch()
            for r in valid_records[i:i + CHUNK]:
                eid = r['emp_id'].strip()
                batch.set(db.collection(collection_name).document(eid), r)
            batch.commit()

        # Rehire 감사 로그 (재입사 이벤트 발생 시에만)
        if rehired:
            try:
                from app.services.audit_service import log_audit
                log_audit(
                    action='nt_rehire_restore',
                    actor=actor,
                    target=collection_name,
                    details={
                        'sheet': sheet_name,
                        'rehired_count': len(rehired),
                        'rehired': rehired[:100],
                        'truncated': len(rehired) > 100,
                    },
                    category='nt_sync',
                )
            except Exception:
                logger.exception('nt_rehire_restore audit_log failed (rehire still succeeded)')

        msg = f'{len(valid_records)} records synced to {collection_name}'
        if rehired:
            msg += f' (rehired {len(rehired)} from retire archive)'
        return True, msg
    except Exception as e:
        logger.exception('Firestore sync failed: %s', e)
        return False, str(e)

def fetch_nt_from_firestore():
    """Firestore에서 전체 NT 데이터 읽기 (활성 6개 컬렉션 합산).
    동일 emp_id 가 여러 컬렉션에 중복 존재할 경우 우선순위(DYB > SUB > CREO > RND)
    대로 하나만 반환 — dedupe_records_by_priority.
    """
    try:
        db = get_firestore_client()
        all_records = []
        for col in NT_COLLECTIONS_BY_PRIORITY:
            for doc in db.collection(col).stream():
                data = doc.to_dict() or {}
                data['_collection'] = col  # dedupe 용 임시 키
                all_records.append(data)
        deduped = dedupe_records_by_priority(all_records)
        for r in deduped:
            r.pop('_collection', None)
        return deduped
    except Exception as e:
        logger.exception('Firestore fetch failed: %s', e)
        return []

def fetch_nickname_from_firestore(emp_id):
    """emp_id로 Firestore에서 nickname 조회. 우선순위 순으로 첫 hit 반환."""
    try:
        if not emp_id:
            return ''
        db = get_firestore_client()
        for col in NT_COLLECTIONS_BY_PRIORITY:
            doc = db.collection(col).document(emp_id).get()
            if doc.exists:
                return doc.to_dict().get('nickname', '')
        return ''
    except Exception as e:
        logger.exception('fetch_nickname_from_firestore failed: %s', e)
        return ''

# 퇴사자 동기화 시 대량 삭제 안전장치 — 활성 인원 대비 retire 요청 비율이 이
# 임계값을 넘으면 abort. 시트 오염·컬럼 밀림·오입력으로 인한 대량 삭제 방지.
_RETIRE_SANITY_CAP_RATIO = 0.30


def _archive_retire_folders(db, validated: list) -> dict:
    """validated 퇴사자들의 활성 컬렉션 문서에서 eval_folder_url 을 읽어 Drive 폴더를
    RETIRED_EVAL_FOLDER_ID 로 이동. 성공한 건에 대해 아카이브 메타데이터 반환.

    반환: {emp_id: {'eval_folder_url', 'eval_folder_id', 'previous_parent_folder_id',
                    'archived_at', 'already_archived'}}
    """
    from app.services.drive_service import move_folder_to_retired, extract_folder_id, EVAL_FOLDER_ID

    archived_map = {}
    if not validated:
        return archived_map

    emp_ids = [r['emp_id'].strip() for r in validated]
    # 모든 (col, eid) 조합을 400 단위로 분할 get_all — Firestore get_all 500건 제한 방어.
    # 외곽 루프를 priority 순으로 두어 중복 emp_id 가 있어도 DYB → SUB → CREO 순서로
    # 먼저 읽히고, 아래 첫 hit 보관 로직 (snap.id not in snap_by_eid) 과 결합해 primary 선택.
    refs = [db.collection(col).document(eid)
            for col in NT_COLLECTIONS_BY_PRIORITY for eid in emp_ids]
    snap_by_eid = {}
    CHUNK = 400
    for i in range(0, len(refs), CHUNK):
        for snap in db.get_all(refs[i:i + CHUNK]):
            if snap.exists and snap.id not in snap_by_eid:
                snap_by_eid[snap.id] = snap.to_dict() or {}

    for r in validated:
        eid = r['emp_id'].strip()
        folder_url = (snap_by_eid.get(eid, {}).get('eval_folder_url') or '').strip()
        folder_id = extract_folder_id(folder_url) if folder_url else None
        if not folder_id:
            logger.info('archive skip %s: no eval_folder_url', eid)
            continue
        result = move_folder_to_retired(folder_id)
        if not result['success']:
            logger.warning('archive %s: Drive move failed', eid)
            continue
        prev = result['previous_parents'][0] if result['previous_parents'] else EVAL_FOLDER_ID
        archived_map[eid] = {
            'eval_folder_url': folder_url,
            'eval_folder_id': folder_id,
            'previous_parent_folder_id': prev,
            'archived_at': firestore.SERVER_TIMESTAMP,
            'already_archived': result.get('already_archived', False),
        }
    return archived_map


def sync_retire_to_firestore(records, actor='unknown'):
    """퇴사자 데이터를 Firestore nt_retire 컬렉션에 저장 + Drive 폴더 아카이브 +
    활성 nt_* 컬렉션에서 stale 문서 제거.

    플로우:
      B) validate — emp_id/retire_date 필수, 현재 활성 컬렉션에 있는 emp_id 는 skip
                    (재입사 상태 — retire 시트에 이력으로 남아있을 뿐, 현재 재직자)
      B-cap) sanity cap — 활성 인원 대비 30% 초과 요청 시 abort
      C) archive — Drive 평가 폴더를 RETIRED_EVAL_FOLDER_ID 로 이동
      D) upsert — nt_retire 에 record + 아카이브 메타데이터 merge 저장
      E) cleanup — 활성 nt_* 컬렉션에서 퇴사자 문서 삭제 (주로 stale)
      F) audit — archived / removed 기록

    재입사 감지/복원은 sync_nt_to_firestore 가 담당. 이 함수는 "현재 재직자가 아닌
    시트 레코드만" 퇴사 처리. 따라서 sync 순서 무관.
    """
    try:
        db = get_firestore_client()

        # 활성 컬렉션 emp_id 수집 — 재직자는 퇴사 처리에서 제외 (새 정책: retire 시트가
        # 이력으로 남아있어도 NT Info 에 있으면 재직자)
        active_emp_ids = set()
        for col in ACTIVE_NT_COLLECTIONS:
            try:
                for doc in db.collection(col).select(['__name__']).stream():
                    active_emp_ids.add(doc.id)
            except Exception:
                logger.exception('active emp_id scan failed for %s', col)

        # ── Phase B: retire_date 검증 + 재직자 제외 ────────────────────────
        validated = []
        invalid = []
        skipped_active = []
        for record in records:
            emp_id = (record.get('emp_id') or '').strip()
            retire_date = (record.get('retire_date') or '').strip()
            if not emp_id:
                continue
            if not retire_date:
                invalid.append(emp_id)
                continue
            if emp_id in active_emp_ids:
                # 재입사 상태 — retire 시트에 이력 남아있지만 NT Info 에 있는 재직자
                skipped_active.append(emp_id)
                continue
            validated.append(record)

        if invalid:
            logger.warning('sync_retire_to_firestore: %d records without retire_date (skipped): %s',
                           len(invalid), invalid[:10])
        if skipped_active:
            logger.info('sync_retire_to_firestore: %d records skipped (currently active, rehired): %s',
                        len(skipped_active), skipped_active[:10])

        retire_ids = [r['emp_id'].strip() for r in validated]

        # ── Phase B-cap: sanity cap ───────────────────────────────────────
        # 새 정책: retire 시트는 누적 이력. 전체 rows 기준이 아니라 "이번 sync 로 신규
        # 추가되는 nt_retire 문서" 만 카운트해야 "한 번에 대량 퇴사 처리" 패턴 탐지됨.
        if retire_ids:
            existing_refs = [db.collection('nt_retire').document(eid) for eid in retire_ids]
            existing_ids = set()
            CHUNK = 400
            for i in range(0, len(existing_refs), CHUNK):
                existing_ids.update(
                    snap.id for snap in db.get_all(existing_refs[i:i + CHUNK]) if snap.exists
                )
            new_retire_count = len(set(retire_ids) - existing_ids)
            active_count = len(active_emp_ids)
            if active_count > 0 and new_retire_count > 0:
                ratio = new_retire_count / active_count
                if ratio > _RETIRE_SANITY_CAP_RATIO:
                    msg = (f'Refusing retire sync: {new_retire_count} NEW retirees requested '
                           f'vs {active_count} active ({ratio:.1%} > {_RETIRE_SANITY_CAP_RATIO:.0%}). '
                           f'Verify retire sheet for data corruption.')
                    logger.error('sync_retire_to_firestore aborted — %s', msg)
                    return False, msg

        # ── Phase C: Drive 폴더 아카이브 (cleanup 전에 수행 — 활성 컬렉션에서
        #              eval_folder_url 을 읽어야 하므로) ──────────────────
        try:
            archived_map = _archive_retire_folders(db, validated)
        except Exception:
            logger.exception('archive phase failed — proceeding without archive metadata')
            archived_map = {}

        # ── Phase D: nt_retire upsert (record + archive 메타데이터 merge) ─
        try:
            retire_batch = db.batch()
            for record in validated:
                eid = record['emp_id'].strip()
                merged = {**record, **archived_map.get(eid, {})}
                retire_batch.set(db.collection('nt_retire').document(eid), merged)
            if validated:
                retire_batch.commit()
        except Exception as e:
            logger.exception('sync_retire_to_firestore upsert failed: %s', e)
            return False, f'Retire upsert failed: {e}'

        # ── Phase E: 활성 컬렉션 cleanup ──────────────────────────────────
        # validated 는 이미 active 제외됐으므로 여기서 삭제 대상은 본래 없어야 함.
        # 그러나 Phase B 수집과 Phase E 실행 사이 race 로 인한 stale 방어용으로 유지.
        removed = []
        try:
            if retire_ids:
                refs = [db.collection(col).document(eid)
                        for col in ACTIVE_NT_COLLECTIONS for eid in retire_ids]
                # get_all 은 400 단위로 분할 — 500건 제한 방어
                CHUNK = 400
                to_delete_refs = []
                for i in range(0, len(refs), CHUNK):
                    to_delete_refs.extend(
                        snap.reference for snap in db.get_all(refs[i:i + CHUNK]) if snap.exists
                    )
                for ref in to_delete_refs:
                    removed.append((ref.parent.id, ref.id))

                for i in range(0, len(to_delete_refs), CHUNK):
                    batch = db.batch()
                    for ref in to_delete_refs[i:i + CHUNK]:
                        batch.delete(ref)
                    batch.commit()
        except Exception as e:
            logger.exception('sync_retire_to_firestore cleanup failed: %s', e)
            return False, f'Retire synced ({len(validated)}) but cleanup failed: {e}'

        # ── Phase F: audit ────────────────────────────────────────────────
        if removed or archived_map:
            try:
                from app.services.audit_service import log_audit
                # archived_at 은 SERVER_TIMESTAMP sentinel — audit 직렬화용으로 제외
                archived_audit = []
                for eid, meta in list(archived_map.items())[:100]:
                    audit_entry = {k: v for k, v in meta.items() if k != 'archived_at'}
                    audit_entry['emp_id'] = eid
                    archived_audit.append(audit_entry)

                log_audit(
                    action='nt_active_cleanup',
                    actor=actor,
                    target='nt_retire_sync',
                    details={
                        'removed_count': len(removed),
                        'removed': [{'col': c, 'emp_id': e} for c, e in removed[:100]],
                        'truncated': len(removed) > 100,
                        'retire_total': len(validated),
                        'invalid_skipped': len(invalid),
                        'active_skipped': len(skipped_active),
                        'archived_count': len(archived_map),
                        'archived_folders': archived_audit,
                        'archived_truncated': len(archived_map) > 100,
                    },
                    category='nt_sync',
                )
            except Exception:
                logger.exception('nt_active_cleanup audit_log failed (sync still succeeded)')

        msg = f'{len(validated)} retire records synced'
        if archived_map:
            msg += f' (archived {len(archived_map)} Drive folders)'
        if skipped_active:
            msg += f' (skipped {len(skipped_active)} rehired)'
        if removed:
            msg += f' (+ {len(removed)} stale active-collection entries removed)'
        if invalid:
            msg += f' (skipped {len(invalid)} missing retire_date)'
        # 전부 invalid 이고 다른 이벤트도 없으면 사실상 no-op → 실패로 처리해 cooldown 불필요.
        if not validated and invalid and not skipped_active:
            return False, f'All {len(invalid)} retire records missing retire_date — sheet may be corrupted.'
        return True, msg
    except Exception as e:
        logger.exception('sync_retire_to_firestore failed: %s', e)
        return False, str(e)

def fetch_retire_from_firestore():
    """Firestore에서 퇴사자 데이터 읽기"""
    try:
        db = get_firestore_client()
        docs = db.collection('nt_retire').stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.exception('fetch_retire_from_firestore failed: %s', e)
        return []

def sync_salary_history_to_firestore(records):
    """동기화 시점의 급여 데이터를 월별 히스토리로 저장"""
    try:
        db = get_firestore_client()
        from datetime import datetime
        month_key = datetime.utcnow().strftime('%Y-%m')
        collection_name = f'nt_salary_history'

        batch = db.batch()
        for record in records:
            emp_id = record.get('emp_id', '').strip()
            if not emp_id or not record.get('total_salary'):
                continue
            ref = db.collection(collection_name).document(month_key).collection('records').document(emp_id)
            batch.set(ref, {
                'emp_id':             emp_id,
                'name':               record.get('name', ''),
                'campus':             record.get('campus', ''),
                'sheet':              record.get('sheet', ''),
                'position':           record.get('position', ''),
                'base_salary':        record.get('base_salary', ''),
                'position_allowance': record.get('position_allowance', ''),
                'role_allowance':     record.get('role_allowance', ''),
                'housing_allowance':  record.get('housing_allowance', ''),
                'total_salary':       record.get('total_salary', ''),
                'synced_at':          month_key,
            })
        batch.commit()
        return True, f'Salary history saved for {month_key}'
    except Exception as e:
        logger.exception('sync_salary_history_to_firestore failed: %s', e)
        return False, str(e)

def fetch_salary_history_from_firestore():
    """월별 급여 히스토리 전체 읽기"""
    try:
        db = get_firestore_client()
        # 최상위 문서 목록(월) 조회
        month_docs = db.collection('nt_salary_history').stream()
        history = {}
        for month_doc in month_docs:
            month_key = month_doc.id
            records = db.collection('nt_salary_history').document(month_key).collection('records').stream()
            history[month_key] = [r.to_dict() for r in records]
        return history
    except Exception as e:
        logger.exception('fetch_salary_history_from_firestore failed: %s', e)
        return {}

# ============================================================================
# SUB CTL 배정 관리 (sub_ctl_assignments 컬렉션)
# ============================================================================

def get_sub_ctl_assignments_map():
    """sub_ctl_assignments에서 {emp_id: campus_code} 딕셔너리 반환"""
    try:
        db = get_firestore_client()
        docs = db.collection('sub_ctl_assignments').stream()
        result = {}
        for doc in docs:
            data = doc.to_dict()
            campus = data.get('campus', '')
            if campus:
                result[doc.id] = campus
        return result
    except Exception as e:
        logger.exception('get_sub_ctl_assignments_map failed: %s', e)
        return {}

def set_sub_ctl_assignments(assignments: list, actor: str, now: str):
    """
    assignments: [{'emp_id': str, 'campus': str, 'prev_campus': str, 'note': str}]
    각 emp_id에 대해 메인 문서 upsert + 히스토리 기록
    """
    try:
        db = get_firestore_client()
        # batch로 메인 문서 set + 히스토리 add를 원자적으로 처리
        # (set 성공 + history add 실패로 이력 유실 방지)
        batch = db.batch()
        for item in assignments:
            emp_id = item['emp_id']
            campus = item['campus']
            prev_campus = item.get('prev_campus', '')
            note = item.get('note', '')
            ref = db.collection('sub_ctl_assignments').document(emp_id)
            batch.set(ref, {'emp_id': emp_id, 'campus': campus, 'assigned_by': actor, 'assigned_at': now})
            history_ref = ref.collection('history').document()  # 자동 생성 ID
            batch.set(history_ref, {
                'campus': campus,
                'prev_campus': prev_campus,
                'assigned_by': actor,
                'assigned_at': now,
                'note': note,
            })
        batch.commit()
        return True, f'{len(assignments)} assignments saved'
    except Exception as e:
        logger.exception('set_sub_ctl_assignments failed: %s', e)
        return False, str(e)

def get_sub_ctl_history(emp_id: str):
    """특정 emp_id의 배정 이력 반환 (최신순)"""
    try:
        db = get_firestore_client()
        docs = db.collection('sub_ctl_assignments').document(emp_id).collection('history').stream()
        history = []
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            history.append(d)
        history.sort(key=lambda x: x.get('assigned_at', ''), reverse=True)
        return history
    except Exception as e:
        logger.exception('get_sub_ctl_history failed: %s', e)
        return []

# ============================================================================
# SUB CTL 회차별 배정 (sub_ctl_session_assignments 컬렉션)
# ============================================================================

_SESSION_ASSIGN_COLLECTION = 'sub_ctl_session_assignments'


def get_session_sub_ctl_map(session_id: str):
    """Return {emp_id: campus_code} for a specific evaluation session.

    campus == '' 은 "이 세션에서 명시적으로 미배정" 의사표시이므로 맵에 포함한다
    (상위 레이어가 default fallback 을 덮어쓸 수 있어야 함).
    """
    try:
        db = get_firestore_client()
        docs = db.collection(_SESSION_ASSIGN_COLLECTION) \
                 .where('session_id', '==', session_id).stream()
        result = {}
        for doc in docs:
            data = doc.to_dict()
            campus = data.get('campus', '')
            emp_id = data.get('emp_id', '')
            if emp_id:
                result[emp_id] = campus
        return result
    except Exception as e:
        logger.exception('get_session_sub_ctl_map failed: %s', e)
        return {}


def set_session_sub_ctl_assignments(session_id: str, assignments: list, actor: str, now: str):
    """Save session-specific SUB CTL assignments.

    assignments: [{'emp_id': str, 'campus': str}]
    """
    try:
        db = get_firestore_client()
        batch = db.batch()
        for item in assignments:
            emp_id = item['emp_id'].strip().lower()
            doc_id = f'{session_id}_{emp_id}'
            ref = db.collection(_SESSION_ASSIGN_COLLECTION).document(doc_id)
            batch.set(ref, {
                'session_id': session_id,
                'emp_id': emp_id,
                'campus': item['campus'],
                'assigned_by': actor,
                'assigned_at': now,
            })
        batch.commit()
        return True, f'{len(assignments)} session assignments saved'
    except Exception as e:
        logger.exception('set_session_sub_ctl_assignments failed: %s', e)
        return False, str(e)


# ============================================================================
# 동기화 쿨다운 관리
# ============================================================================
SYNC_COOLDOWNS = {
    'nt': 10,       # 10분
    'retire': 60,   # 60분
    'salary': 60,   # 60분
}

def get_sync_status(sync_type):
    """동기화 상태 조회 (마지막 시각 + 쿨다운 여부)"""
    try:
        from datetime import datetime, timezone
        db = get_firestore_client()
        doc = db.collection('nt_sync_status').document(sync_type).get()
        if not doc.exists:
            return {'last_synced': None, 'can_sync': True, 'remaining_minutes': 0}

        data = doc.to_dict()
        last_synced = data.get('last_synced')  # ISO string
        cooldown_minutes = SYNC_COOLDOWNS.get(sync_type, 60)

        if not last_synced:
            return {'last_synced': None, 'can_sync': True, 'remaining_minutes': 0}

        from datetime import timedelta
        last_dt = datetime.fromisoformat(last_synced)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_dt).total_seconds() / 60
        remaining = max(0, cooldown_minutes - elapsed)

        return {
            'last_synced': last_synced,
            'can_sync': remaining <= 0,
            'remaining_minutes': round(remaining, 1)
        }
    except Exception as e:
        logger.exception('get_sync_status failed: %s', e)
        return {'last_synced': None, 'can_sync': True, 'remaining_minutes': 0}


def update_sync_status(sync_type):
    """동기화 완료 시각 저장"""
    try:
        from datetime import datetime, timezone
        db = get_firestore_client()
        now = datetime.now(timezone.utc).isoformat()
        db.collection('nt_sync_status').document(sync_type).set({
            'last_synced': now,
            'sync_type': sync_type
        })
        return now
    except Exception as e:
        logger.exception('update_sync_status failed: %s', e)
        return None