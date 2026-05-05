"""
app/eval_v2/api/annual_eval/config.py
Annual Eval Config API — 평가 설정 (raters, score_weights, raise_policy)
"""
import logging
from flask import request
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now
from app.constants import COL_NHR_ANNUAL_EVAL_CONFIG
from app.utils.response import success, error
from app.services.firebase_service import get_firestore_client
from ._helpers import require_xhr, _admin_email

logger = logging.getLogger(__name__)


@eval_v2_api.route('/annual-eval/config', methods=['GET'])
@api_admin_required
def api_annual_eval_config_get():
    """nhr_annual_eval_config/settings 조회"""
    try:
        db = get_firestore_client()
        doc = db.collection(COL_NHR_ANNUAL_EVAL_CONFIG).document('settings').get()
        if doc.exists:
            cfg = doc.to_dict()
        else:
            cfg = {
                'raters': [],
                'score_weights': {'reg_eval': 50, 'obs_eval': 30, 'net_eval': 20},
                'raise_policy': [],
            }
        return success({'config': cfg})
    except Exception:
        logger.exception('api_annual_eval_config_get error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/config', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_config_save():
    """nhr_annual_eval_config/settings 저장 (raters, score_weights, raise_policy)"""
    try:
        data = request.get_json(silent=True) or {}
        db = get_firestore_client()

        update = {}
        if 'raters' in data:
            if not isinstance(data['raters'], list):
                return error('raters must be a list.', 400)
            update['raters'] = [str(r).strip() for r in data['raters'] if str(r).strip()]

        if 'score_weights' in data:
            sw = data['score_weights']
            if not isinstance(sw, dict):
                return error('score_weights must be an object.', 400)
            if not all(isinstance(sw.get(k), int) for k in ('reg_eval', 'obs_eval', 'net_eval')):
                return error('score_weights values must be integers.', 400)
            try:
                reg  = int(sw['reg_eval'])
                obs  = int(sw['obs_eval'])
                net  = int(sw['net_eval'])
            except (ValueError, TypeError):
                return error('score_weights values must be integers.', 400)
            if reg < 0 or obs < 0 or net < 0:
                return error('score_weights must be non-negative.', 400)
            if reg + obs + net != 100:
                return error('score_weights must sum to 100.', 400)
            update['score_weights'] = {'reg_eval': reg, 'obs_eval': obs, 'net_eval': net}

        if 'raise_policy' in data:
            if not isinstance(data['raise_policy'], list):
                return error('raise_policy must be a list.', 400)
            policy = []
            for item in data['raise_policy']:
                if not isinstance(item, dict):
                    continue
                try:
                    base_min = int(item.get('base_min', 0))
                    base_max_raw = item.get('base_max', 0)
                    base_max = int(base_max_raw) if base_max_raw else 0
                except (ValueError, TypeError):
                    return error('Invalid raise_policy base range.', 400)
                if base_min < 0 or base_max < 0:
                    return error('Base salary ranges must be non-negative.', 400)

                tiers_raw = item.get('tiers', [])
                if not isinstance(tiers_raw, list):
                    return error('raise_policy tiers must be a list.', 400)
                tiers = []
                for tier in tiers_raw:
                    if not isinstance(tier, dict):
                        continue
                    try:
                        score_min = float(tier.get('score_min', 0))
                        score_max = float(tier.get('score_max', 100))
                    except (ValueError, TypeError):
                        return error('Invalid tier score range.', 400)
                    if not (0.0 <= score_min <= 100.0 and 0.0 <= score_max <= 100.0):
                        return error('Score ranges must be between 0 and 100.', 400)
                    if score_min > score_max:
                        return error('score_min must not exceed score_max.', 400)
                    try:
                        tiers.append({
                            'score_min':    score_min,
                            'score_max':    score_max,
                            'base_inc':     int(tier.get('base_inc', 0)),
                            'manual_input': bool(tier.get('manual_input', False)),
                            'note':         str(tier.get('note', '')).strip()[:200],
                        })
                    except (ValueError, TypeError):
                        return error('Invalid tier entry.', 400)

                # tier 정렬 + 인접 overlap 검증 — 매칭 시 입력 순서 의존 제거
                tiers.sort(key=lambda t: t['score_min'])
                for _i in range(len(tiers) - 1):
                    _cur = tiers[_i]
                    _nxt = tiers[_i + 1]
                    # 양쪽 inclusive 매칭이라 score_max >= 다음 score_min 이면 경계값 모호
                    if _cur['score_max'] >= _nxt['score_min']:
                        _bmax = base_max if base_max else '∞'
                        return error(
                            f'Tier ranges overlap: [{_cur["score_min"]}-{_cur["score_max"]}] and '
                            f'[{_nxt["score_min"]}-{_nxt["score_max"]}] in base group '
                            f'{base_min}-{_bmax}. Adjust score_max so it is strictly less than the next score_min.',
                            400)

                policy.append({
                    'base_min': base_min,
                    'base_max': base_max,
                    'label':    str(item.get('label', '')).strip()[:100],
                    'tiers':    tiers,
                })
            update['raise_policy'] = policy

        if not update:
            return error('Nothing to update.', 400)

        update['updated_at']  = kst_now()
        update['updated_by']  = _admin_email()
        db.collection(COL_NHR_ANNUAL_EVAL_CONFIG).document('settings').set(update, merge=True)
        return success()
    except Exception:
        logger.exception('api_annual_eval_config_save error')
        return error('An internal error occurred.', 500)
