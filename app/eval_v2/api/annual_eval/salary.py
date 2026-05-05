"""
app/eval_v2/api/annual_eval/salary.py
급여 계산 및 평가 사이클 계산 헬퍼
"""
import logging
from datetime import date as _date, timedelta as _timedelta

from app.services.firebase_service import get_firestore_client
from app.utils.time_utils import kst_date
from ._helpers import _NT_COLLECTIONS

logger = logging.getLogger(__name__)


def _calc_eval_cycle(nt_start_date_str: str, today_date=None):
    """
    입사일 기준 현재 평가 사이클 계산.

    Returns:
        (eval_deadline: str, eval_sequence: int, days_remaining: int)
        입력이 유효하지 않으면 (None, None, None)

    eval_deadline  = 다음 기념일 하루 전 (YYYY-MM-DD)
    eval_sequence  = 몇 번째 평가 (1-based)
    days_remaining = 오늘부터 deadline까지 남은 일수 (0 = 오늘이 deadline)
    """
    if today_date is None:
        today_date = kst_date()

    try:
        if hasattr(nt_start_date_str, 'year'):
            start = _date(nt_start_date_str.year, nt_start_date_str.month, nt_start_date_str.day)
        else:
            s = str(nt_start_date_str).strip()[:10].replace('.', '-').replace('/', '-')
            start = _date.fromisoformat(s)
    except (ValueError, TypeError, AttributeError):
        return None, None, None

    if start >= today_date:
        try:
            anniversary = _date(start.year + 1, start.month, start.day)
        except ValueError:
            anniversary = _date(start.year + 1, 3, 1)
        deadline = anniversary - _timedelta(days=1)
        return deadline.isoformat(), 1, (deadline - today_date).days

    years_since = today_date.year - start.year
    if (today_date.month, today_date.day) >= (start.month, start.day):
        years_completed = years_since
    else:
        years_completed = years_since - 1

    seq = years_completed + 1
    try:
        anniversary = _date(start.year + seq, start.month, start.day)
    except ValueError:
        anniversary = _date(start.year + seq, 3, 1)

    deadline = anniversary - _timedelta(days=1)
    days_remaining = (deadline - today_date).days

    return deadline.isoformat(), seq, days_remaining


def _resolve_current_cycle(emp_id, nt_start_date, today_date,
                           record_lookup, grace_days=14):
    """
    '현재 활성 사이클'을 결정. 기본은 `_calc_eval_cycle` 의 ideal 사이클이지만,
    기념일이 막 지나 ideal 문서는 없고 이전 사이클이 미완 상태라면 grace_days 이내
    한정으로 이전 사이클을 계속 작업 가능하도록 반환.

    record_lookup: callable(doc_id: str) -> dict | None
        - list endpoint: `all_records.get` (이미 스트리밍된 전체 dict 조회)
        - record endpoint: Firestore 단건 fetch 래퍼

    Returns dict 또는 None (start_date 파싱 실패 시):
      {
        'eval_deadline':     str,      # 활성 사이클 deadline
        'eval_sequence':     int,      # 활성 사이클 seq (grace 시 ideal-1)
        'days_remaining':    int,      # 활성 deadline 까지 남은 일수 (grace 시 음수)
        'resolved_doc_id':   str,
        'resolved_record':   dict | None,
        'grace_active':      bool,
        'grace_days_left':   int | None,  # grace_active 일 때만 값
        'ideal_deadline':    str,      # 롤오버 후 ideal 사이클 deadline (UI 정보용)
        'ideal_sequence':    int,
      }
    """
    ideal_deadline, ideal_seq, ideal_days_rem = _calc_eval_cycle(nt_start_date, today_date)
    if not ideal_deadline:
        return None

    ideal_doc_id = f'{emp_id}__{ideal_deadline}'
    ideal_rec    = record_lookup(ideal_doc_id)

    # ideal 문서가 이미 존재하거나 seq==1 (이전 사이클 없음) 이면 grace 불가
    if ideal_rec is not None or ideal_seq <= 1:
        return {
            'eval_deadline':   ideal_deadline,
            'eval_sequence':   ideal_seq,
            'days_remaining':  ideal_days_rem,
            'resolved_doc_id': ideal_doc_id,
            'resolved_record': ideal_rec,
            'grace_active':    False,
            'grace_days_left': None,
            'ideal_deadline':  ideal_deadline,
            'ideal_sequence':  ideal_seq,
        }

    # 이전 사이클 deadline 계산 (`_calc_eval_cycle` 와 동일한 Feb-29→Mar-1 폴백)
    try:
        if hasattr(nt_start_date, 'year'):
            start = _date(nt_start_date.year, nt_start_date.month, nt_start_date.day)
        else:
            s = str(nt_start_date).strip()[:10].replace('.', '-').replace('/', '-')
            start = _date.fromisoformat(s)
    except (ValueError, TypeError, AttributeError):
        return None

    try:
        prev_anniv = _date(start.year + ideal_seq - 1, start.month, start.day)
    except ValueError:
        prev_anniv = _date(start.year + ideal_seq - 1, 3, 1)

    prev_deadline_date = prev_anniv - _timedelta(days=1)
    prev_deadline      = prev_deadline_date.isoformat()
    prev_doc_id        = f'{emp_id}__{prev_deadline}'
    prev_rec           = record_lookup(prev_doc_id)
    days_past          = (today_date - prev_deadline_date).days

    grace_eligible = (prev_rec is not None
                      and prev_rec.get('status') != 'done'
                      and 0 < days_past <= grace_days)

    if grace_eligible:
        return {
            'eval_deadline':   prev_deadline,
            'eval_sequence':   ideal_seq - 1,
            'days_remaining':  -days_past,
            'resolved_doc_id': prev_doc_id,
            'resolved_record': prev_rec,
            'grace_active':    True,
            'grace_days_left': grace_days - days_past,
            'ideal_deadline':  ideal_deadline,
            'ideal_sequence':  ideal_seq,
        }

    # Grace 불가 — ideal 반환 (좀비는 History 탭에서만 조회됨)
    return {
        'eval_deadline':   ideal_deadline,
        'eval_sequence':   ideal_seq,
        'days_remaining':  ideal_days_rem,
        'resolved_doc_id': ideal_doc_id,
        'resolved_record': None,
        'grace_active':    False,
        'grace_days_left': None,
        'ideal_deadline':  ideal_deadline,
        'ideal_sequence':  ideal_seq,
    }


def _get_nt_salary(emp_id: str) -> dict:
    """
    NT Info Firestore에서 급여 초기값 조회 → 만 원 단위로 변환.
    반환: {base_current, pos_current, role_current, housing_current, total_current, nt_*}
    """
    db = get_firestore_client()
    emp_id_stripped = emp_id.strip()
    _id_candidates = list(dict.fromkeys(filter(None, [
        emp_id_stripped,
        emp_id_stripped.lower(),
        emp_id_stripped.upper(),
        emp_id_stripped[0].upper() + emp_id_stripped[1:] if emp_id_stripped else '',
    ])))
    # _NT_COLLECTIONS 는 priority 순 튜플 (DYB > SUB > CREO > RND). 외곽 루프를
    # 컬렉션으로 두어 중복 사번 발생 시 DYB 값이 먼저 조회됨. get_all 반환 순서는
    # refs 와 동일하므로 아래 first-exists break 가 priority 순 first hit 를 보장.
    refs = [db.collection(col).document(cand)
            for col in _NT_COLLECTIONS for cand in _id_candidates]
    record = {}
    for snap in db.get_all(refs):
        if snap.exists:
            record = snap.to_dict()
            break

    def to_man(val) -> int:
        """원 → 만원 변환. 만원 단위(< 10,000)는 그대로, 원 단위(≥ 10,000)는 나눔."""
        try:
            v = int(float(str(val).replace(',', '').strip()))
            if v <= 0:
                return 0
            return v // 10000 if v >= 10000 else v
        except (ValueError, TypeError):
            return 0

    base    = to_man(record.get('base_salary', 0))
    pos     = to_man(record.get('position_allowance', 0))
    role    = to_man(record.get('role_allowance', 0))
    housing = to_man(record.get('housing_allowance', 0))

    raw_start = record.get('start_date', '')
    normalized_start = ''
    if hasattr(raw_start, 'year'):
        try:
            normalized_start = _date(raw_start.year, raw_start.month, raw_start.day).isoformat()
        except (ValueError, TypeError, AttributeError):
            logger.warning('_get_nt_salary: cannot parse start_date object %r', raw_start)
    elif raw_start:
        s = str(raw_start).strip()[:10].replace('.', '-').replace('/', '-')
        try:
            _date.fromisoformat(s)
            normalized_start = s
        except (ValueError, TypeError):
            logger.warning('_get_nt_salary: unparseable start_date string %r (emp_id may be unknown)', raw_start)

    return {
        'base_current':    base,
        'pos_current':     pos,
        'role_current':    role,
        'housing_current': housing,
        'total_current':   base + pos + role + housing,
        'nt_name':         record.get('name', ''),
        'nt_campus':       record.get('campus', ''),
        'nt_position':     record.get('position', ''),
        'nt_start_date':   normalized_start,
        'nt_nationality':  record.get('nationality', ''),
        'salary_day':      record.get('salary_day', ''),
        'allowance_name':  str(record.get('allowance_name') or '').strip(),
    }
