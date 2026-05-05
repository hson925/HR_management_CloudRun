import logging
import re
from app.eval_v2.blueprints import eval_v2_api  # noqa: F401 (imported for cache context)

logger = logging.getLogger(__name__)
from app.extensions import cache
from app.eval_v2.questions import DEFAULT_QUESTIONS, EVAL_TYPE_LABELS
from app.services.firebase_service import get_firestore_client
from app.utils.time_utils import kst_now
from app.constants import (
    COL_EVAL_V2_CONFIG,
    COL_EVAL_V2_RESPONSES,
    COL_EVAL_V2_SUMMARIES,
)


# 사용자 입력 정규식 — eval_v2/api/* 공용. annual_eval/_helpers.py 의 _EMP_ID_RE
# 와 동일 정의 (private 침범 회피 위해 공용 위치에 재정의).
EMP_ID_RE     = re.compile(r'^[a-zA-Z0-9_\-]{1,30}$')
SESSION_ID_RE = re.compile(r'^[^/]{1,100}$')


_BATCH_LIMIT = 500


def _batch_delete(db, docs: list) -> int:
    """Firestore batch 삭제 (_BATCH_LIMIT 단위). 삭제된 건수 반환."""
    deleted = 0
    for i in range(0, len(docs), _BATCH_LIMIT):
        batch = db.batch()
        for doc in docs[i:i + _BATCH_LIMIT]:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs[i:i + _BATCH_LIMIT])
    return deleted


_VALID_EVAL_TYPES = set(EVAL_TYPE_LABELS.keys())
_MAX_TEXT_LEN     = 2000
_MAX_NAME_LEN     = 100

# ── 점수별 설명 + 가변 척도 ─────────────────────────────────
_MIN_MAX_SCORE     = 2
_MAX_MAX_SCORE     = 10
_DEFAULT_MAX_SCORE = 5
_MAX_DESC_LEN      = 300


@cache.memoize(timeout=3600)
def get_config(eval_type, config_type):
    try:
        db = get_firestore_client()
        doc = db.collection(COL_EVAL_V2_CONFIG).document(config_type).collection('data').document(eval_type).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        logger.exception('get_config error: %s', e)
        return None


def get_questions(eval_type):
    saved = get_config(eval_type, 'questions')
    if saved and saved.get('roles'):
        return saved['roles']
    return DEFAULT_QUESTIONS.get(eval_type, {}).get('roles', [])


def load_snapshot_questions(snapshot: dict, eval_type: str):
    """스냅샷에서 roles 리스트 로드, 없으면 글로벌 config fallback."""
    roles = snapshot.get(eval_type, {}).get('questions') if snapshot else None
    return roles if roles else get_questions(eval_type)


def load_snapshot_weights(snapshot: dict, eval_type: str):
    """스냅샷에서 weights 로드, 없으면 글로벌 config fallback."""
    weights = snapshot.get(eval_type, {}).get('weights') if snapshot else None
    return weights if weights else get_weights(eval_type)


def extract_valid_qids(roles_list: list) -> set:
    """roles 리스트에서 모든 question ID + open_question ID 추출."""
    qids = set()
    for role in (roles_list or []):
        if not isinstance(role, dict):
            continue
        for q in role.get('questions', role.get('items', [])):
            qid = q.get('id') if isinstance(q, dict) else q
            if qid:
                qids.add(qid)
        for oq in role.get('open_questions', []):
            oqid = oq.get('id') if isinstance(oq, dict) else oq
            if oqid:
                qids.add(oqid)
    return qids


def extract_max_scores(roles_list: list) -> dict:
    """roles 리스트에서 {qid: max_score_int} 추출. 모든 valid qid 키 보장.
    max_score 누락/유효성 실패 시 _DEFAULT_MAX_SCORE 폴백.
    open_questions 는 점수 척도가 없으므로 제외.
    """
    out = {}
    for role in (roles_list or []):
        if not isinstance(role, dict):
            continue
        for q in role.get('questions', role.get('items', [])):
            if not isinstance(q, dict):
                continue
            qid = q.get('id')
            if not qid:
                continue
            raw = q.get('max_score')
            try:
                v = int(raw)
                if not (_MIN_MAX_SCORE <= v <= _MAX_MAX_SCORE):
                    v = _DEFAULT_MAX_SCORE
            except (ValueError, TypeError):
                v = _DEFAULT_MAX_SCORE
            out[qid] = v
    return out


# ── Firestore query helpers ───────────────────────────────────────────────────
def query_responses(session_id=None, emp_id=None, eval_type=None, include_test=False):
    """
    eval_v2_responses 조회 헬퍼. 지정된 필터 조합으로 .where() chaining 후 list[dict] 반환.
    is_test 문서는 기본 제외. Firestore 조합 인덱스 요구사항은 호출측과 동일 (필요 시 firestore.indexes.json).
    """
    db = get_firestore_client()
    q = db.collection(COL_EVAL_V2_RESPONSES)
    if session_id is not None:
        q = q.where('session_id', '==', session_id)
    if emp_id is not None:
        q = q.where('emp_id', '==', emp_id)
    if eval_type is not None:
        q = q.where('eval_type', '==', eval_type)
    out = []
    for doc in q.stream():
        d = doc.to_dict()
        if not include_test and d.get('is_test', False):
            continue
        out.append(d)
    return out


def query_summaries(session_id=None, emp_id=None):
    """eval_v2_summaries 조회. list[dict] 반환."""
    db = get_firestore_client()
    q = db.collection(COL_EVAL_V2_SUMMARIES)
    if session_id is not None:
        q = q.where('session_id', '==', session_id)
    if emp_id is not None:
        q = q.where('emp_id', '==', emp_id)
    return [doc.to_dict() for doc in q.stream()]


def get_weights(eval_type):
    saved = get_config(eval_type, 'weights')
    if saved and saved.get('weights'):
        return saved['weights']
    roles = DEFAULT_QUESTIONS.get(eval_type, {}).get('roles', [])
    if not roles:
        return {}
    base = round(100 / len(roles), 1)
    result = {}
    for r in roles:
        if isinstance(r, dict):
            key = r.get('name') or r.get('role')
            if key:
                result[key] = base
    return result
