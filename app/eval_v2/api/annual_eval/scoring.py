"""
app/eval_v2/api/annual_eval/scoring.py
평가 점수 계산 헬퍼 (세션 점수, 종합 점수, 기여 점수)
"""
import logging

from app.eval_v2.api.common import load_snapshot_weights, load_snapshot_questions, extract_max_scores
from app.constants import COL_EVAL_V2_SESSIONS, COL_EVAL_V2_RESPONSES
from app.services.firebase_service import get_firestore_client

logger = logging.getLogger(__name__)


def _calc_session_score(emp_id: str, eval_type: str, session_id: str) -> float | None:
    """
    eval_v2_responses에서 특정 회차의 교사 점수를 계산.
    세션 스냅샷 가중치 적용 후 100점 환산 (×20).
    응답 없으면 None 반환.

    eval_type 동작: 빈 문자열이면 eval_type 필터 없이 조회 → 매치된 첫 문서의
    eval_type으로 가중치 로드. 프론트가 teacher 역할을 모를 때(annual eval 기본값
    'regular'는 Position/TL/STL/SUB에 부정확) 잘못된 필터로 빈 결과가 나오지 않도록.
    """
    if not session_id or session_id == '__manual__':
        return None
    db = get_firestore_client()

    emp_id_stripped = emp_id.strip()
    variants = list(dict.fromkeys([emp_id_stripped, emp_id_stripped.upper(), emp_id_stripped.lower()]))
    docs_list = []
    for variant in variants:
        q = (db.collection(COL_EVAL_V2_RESPONSES)
               .where('emp_id', '==', variant)
               .where('session_id', '==', session_id))
        if eval_type:
            q = q.where('eval_type', '==', eval_type)
        docs_list = list(q.stream())
        if docs_list:
            break

    if not docs_list:
        return None

    effective_eval_type = eval_type or (docs_list[0].to_dict().get('eval_type', '') if docs_list else '')

    snapshot = {}
    try:
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if sess_doc.exists:
            snapshot = sess_doc.to_dict().get('questions_snapshot', {})
    except Exception:
        logger.exception('_calc_session_score: snapshot load failed [%s]', session_id)

    weights_raw = load_snapshot_weights(snapshot, effective_eval_type)
    weights = {}
    for k, v in weights_raw.items():
        try:
            fv = float(v)
            weights[k] = fv / 100 if fv > 1 else fv
        except (ValueError, TypeError):
            pass

    # 가변 max_score 지원 — qid 별 max_score 맵 (snapshot 폴백 포함, 누락 시 기본값)
    roles_list = load_snapshot_questions(snapshot, effective_eval_type)
    qid_max = extract_max_scores(roles_list)

    # 같은 (emp/역할/이름) 그룹의 최신 1건만 채택 — 재제출 / 동명이인 dupe 제외.
    # report_service 의 build_report_context / _calc_ranks 와 동일 필터로 일관성 보장.
    from app.services.report_service import select_effective_responses
    raw_dicts = [doc.to_dict() for doc in docs_list]
    effective = select_effective_responses(raw_dicts)

    role_scores: dict[str, list[float]] = {}
    for d in effective:
        role = d.get('rater_role', '')
        norm_vals = []  # 0-1 정규화 점수
        for qid, raw_v in (d.get('scores', {}) or {}).items():
            try:
                fv = float(raw_v)
            except (ValueError, TypeError):
                continue
            # 응답값 0 / 음수는 "미응답 / 무효" 로 간주.
            # 평가 폼이 1점 이상으로 강제하므로 0 = 미선택 만 발생.
            # admin manual entry 도 동일 검증 적용.
            if fv <= 0:
                continue
            m = float(qid_max.get(qid, 5))  # 폴백: 5점
            if m <= 0:
                continue
            norm_vals.append(min(fv, m) / m)
        if norm_vals:
            role_scores.setdefault(role, []).append(sum(norm_vals) / len(norm_vals))

    if not role_scores:
        return None

    role_avgs = {r: sum(vals) / len(vals) for r, vals in role_scores.items()}
    weighted_sum = 0.0
    weight_total = 0.0
    for role, avg in role_avgs.items():
        w = weights.get(role, 0)
        weighted_sum += avg * w
        weight_total += w

    if weight_total == 0:
        avgs = list(role_avgs.values())
        raw = sum(avgs) / len(avgs) if avgs else 0.0
    else:
        raw = weighted_sum / weight_total

    # raw 가 이미 0-1 정규화된 평균 → ×100 으로 100점 환산
    return round(raw * 100, 2)


def _calc_composite(record: dict, weights: dict) -> float | None:
    """
    3가지 점수(reg_final_score, obs_score, net_score)를 가중 합산.
    None인 항목은 가중치에서 제외 후 재정규화. 0.0은 유효한 점수로 포함.
    데이터가 없으면(모두 None) None 반환.
    weights: {reg_eval: int, obs_eval: int, net_eval: int} (합 100)
    """
    fields = [
        ('reg_final_score', weights.get('reg_eval', 0)),
        ('obs_score',       weights.get('obs_eval', 0)),
        ('net_score',       weights.get('net_eval', 0)),
    ]
    weighted_sum = 0.0
    weight_total = 0.0
    for field, w in fields:
        val = record.get(field)
        if val is not None:
            weighted_sum += float(val) * w
            weight_total += w
    if weight_total == 0:
        return None
    return round(weighted_sum / weight_total, 2)


def _calc_contributions(record: dict, weights: dict) -> dict:
    """
    재정규화된 가중치로 각 영역 기여 점수 계산.
    composite = sum(contributions) 이 되도록 보장.
    """
    fields = [
        ('reg_final_score', weights.get('reg_eval', 0), 'reg_contrib'),
        ('obs_score',       weights.get('obs_eval', 0), 'obs_contrib'),
        ('net_score',       weights.get('net_eval', 0), 'net_contrib'),
    ]
    active_weight = sum(w for f, w, _ in fields if record.get(f) is not None)
    result = {}
    for field, w, key in fields:
        val = record.get(field)
        if val is not None and active_weight > 0:
            result[key] = round(float(val) * w / active_weight, 2)
        else:
            result[key] = 0
    return result
