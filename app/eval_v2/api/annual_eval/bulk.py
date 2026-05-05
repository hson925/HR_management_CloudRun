"""
app/eval_v2/api/annual_eval/bulk.py
Annual Eval Bulk API — 보고서 일괄 생성
"""
import logging
import os
import re as _re
from flask import request
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now
from app.constants import (
    COL_EVAL_V2_SESSIONS,
    COL_NHR_ANNUAL_EVAL, COL_NHR_ANNUAL_EVAL_CONFIG,
)
from app.utils.response import success, error
from app.services.firebase_service import get_firestore_client
from app.extensions import cache
from ._helpers import require_xhr, _admin_email
from .scoring import _calc_contributions, _calc_composite

logger = logging.getLogger(__name__)


@eval_v2_api.route('/annual-eval/bulk-generate', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_bulk_generate():
    """
    status='done'인 교사 전체(또는 지정 목록)의 보고서를 일괄 생성.
    요청: {emp_ids: [str] (선택, 없으면 done 상태 전체)}
    """
    try:
        from app.services.report_service import html_to_pdf
        from app.services.drive_service import upload_report_to_eval_folder, preload_bv_url_map
        from app.services.nt_cache_service import get_nt_record
        from jinja2 import Environment, FileSystemLoader

        # NT INFO BV열을 한 번에 로드하여 교사별 Sheets API 반복 호출 방지
        bv_url_map = preload_bv_url_map()

        data = request.get_json(silent=True) or {}
        target_ids = data.get('emp_ids')

        db = get_firestore_client()

        if target_ids and isinstance(target_ids, list):
            docs = []
            for eid in target_ids:
                for snap in db.collection(COL_NHR_ANNUAL_EVAL) \
                              .where('emp_id', '==', eid.strip()) \
                              .stream():
                    docs.append(snap)
        else:
            docs = list(db.collection(COL_NHR_ANNUAL_EVAL)
                         .where('status', '==', 'done')
                         .stream())

        if not docs:
            return success({'generated': 0, 'skipped': 0, 'results': []})

        cfg_doc = db.collection(COL_NHR_ANNUAL_EVAL_CONFIG).document('settings').get()
        config  = cfg_doc.to_dict() if cfg_doc.exists else {}
        weights = config.get('score_weights', {'reg_eval': 50, 'obs_eval': 30, 'net_eval': 20})

        template_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'templates', 'eval_v2')
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
        template = env.get_template('annual_eval_report.html')

        results = []
        generated = 0

        _session_label_cache: dict[str, str] = {}
        def _get_label(sid: str) -> str:
            if sid == '__manual__':
                return '직접 입력'
            if not sid:
                return ''
            if sid in _session_label_cache:
                return _session_label_cache[sid]
            try:
                s = db.collection(COL_EVAL_V2_SESSIONS).document(sid).get()
                label = s.to_dict().get('label', sid) if s.exists else sid
            except Exception:
                label = sid
            _session_label_cache[sid] = label
            return label

        from app.utils.time_utils import kst_date
        from datetime import datetime, timezone, timedelta
        from .reports import _calc_tenure, _match_policy_rank
        from app.services.asset_service import get_static_data_uri
        KST = timezone(timedelta(hours=9))
        now_kst = datetime.now(KST)
        issue_date     = now_kst.strftime('%Y-%m-%d')
        issue_time     = now_kst.strftime('%H:%M')
        issue_datetime = f'{issue_date} {issue_time}'
        today_kst      = kst_date()
        logo_data_uri  = get_static_data_uri('logo.png')

        for snap in docs:
            record = snap.to_dict()
            emp_id = record.get('emp_id', '')
            eval_deadline = record.get('eval_deadline', '')
            eval_sequence = record.get('eval_sequence', '')
            doc_id = snap.id

            try:
                from .salary import _get_nt_salary
                salary_info = _get_nt_salary(emp_id)
                nt_rec = get_nt_record(emp_id) or {}
                teacher_name = (salary_info.get('nt_name', '')
                                or nt_rec.get('name', '')
                                or record.get('nt_name', '')
                                or '')
                info_campus      = (salary_info.get('nt_campus') or record.get('nt_campus') or record.get('campus') or nt_rec.get('campus') or '')
                info_nationality = (salary_info.get('nt_nationality') or record.get('nt_nationality') or nt_rec.get('nationality') or '')
                info_start_date  = (salary_info.get('nt_start_date') or record.get('nt_start_date') or nt_rec.get('start_date') or '')
                info_salary_day  = (salary_info.get('salary_day') or nt_rec.get('salary_day') or record.get('salary_day') or '')

                tenure_text = _calc_tenure(info_start_date, today_kst)
                allowance_name_raw = str(nt_rec.get('allowance_name') or '').strip()
                if allowance_name_raw:
                    current_allowances = [
                        s.strip() for s in _re.split(r'[,\n/、·]+', allowance_name_raw) if s.strip()
                    ]
                else:
                    current_allowances = []
                    if (record.get('base_current')    or 0) > 0: current_allowances.append('기본급')
                    if (record.get('pos_current')     or 0) > 0: current_allowances.append('직책 수당')
                    if (record.get('role_current')    or 0) > 0: current_allowances.append('역할 수당')
                    if (record.get('housing_current') or 0) > 0: current_allowances.append('주거 수당')

                composite_score_val = record.get('composite_score')
                if composite_score_val is None:
                    composite_score_val = _calc_composite(record, weights)
                raise_policy   = config.get('raise_policy') or []
                composite_rank = _match_policy_rank(record.get('base_current'), composite_score_val, raise_policy)
                ctx = {
                    'emp_id':           emp_id.upper(),
                    'eval_deadline':    eval_deadline,
                    'eval_sequence':    eval_sequence,
                    'issue_date':       issue_date,
                    'issue_time':       issue_time,
                    'issue_datetime':   issue_datetime,
                    'teacher_name':     teacher_name,
                    'logo_data_uri':    logo_data_uri,
                    'tenure_text':      tenure_text,
                    'current_allowances': current_allowances,
                    'nt_nationality':   info_nationality,
                    'nt_start_date':    info_start_date,
                    'salary_day':       info_salary_day,
                    'composite_rank':   composite_rank,
                    'campus':           info_campus,
                    'eval_type':        record.get('eval_type', ''),
                    'status':           record.get('status', ''),
                    'session_1_label':  _get_label(record.get('session_1_id', '')),
                    'session_2_label':  _get_label(record.get('session_2_id', '')),
                    'session_1_id':     record.get('session_1_id', ''),
                    'session_2_id':     record.get('session_2_id', ''),
                    'reg_score_1':      record.get('reg_score_1'),
                    'reg_score_2':      record.get('reg_score_2'),
                    'reg_final_score':  record.get('reg_final_score'),
                    'obs_score':        record.get('obs_score'),
                    'obs_date':         record.get('obs_date', ''),
                    'obs_rater':        record.get('obs_rater', ''),
                    'obs_link':         record.get('obs_link', ''),
                    'obs_eng':          record.get('obs_eng', ''),
                    'net_score':        record.get('net_score'),
                    'net_date':         record.get('net_date', ''),
                    'net_rater':        record.get('net_rater', ''),
                    'net_link':         record.get('net_link', ''),
                    'net_eng':          record.get('net_eng', ''),
                    'composite_score':  composite_score_val,
                    'other_eng':        record.get('other_eng', ''),
                    'obs_eng_ko':       record.get('obs_eng_ko', ''),
                    'net_eng_ko':       record.get('net_eng_ko', ''),
                    'other_eng_ko':     record.get('other_eng_ko', ''),
                    'allowance_comment': record.get('allowance_comment', ''),
                    'base_current':     record.get('base_current', 0),
                    'pos_current':      record.get('pos_current', 0),
                    'role_current':     record.get('role_current', 0),
                    'housing_current':  record.get('housing_current', 0),
                    'total_current':    record.get('total_current', 0),
                    'base_inc':         record.get('base_inc', 0),
                    'pos_inc':          record.get('pos_inc', 0),
                    'role_inc':         record.get('role_inc', 0),
                    'housing_inc':      record.get('housing_inc', 0),
                    'applied_total':    record.get('applied_total', 0),
                    'weight_reg':       weights.get('reg_eval', 50),
                    'weight_obs':       weights.get('obs_eval', 30),
                    'weight_net':       weights.get('net_eval', 20),
                    **_calc_contributions(record, weights),
                }

                html_content = template.render(**ctx)
                pdf_bytes = html_to_pdf(html_content)

                safe_name = _re.sub(r'[^a-zA-Z0-9가-힣\s_\-]', '', teacher_name).strip()
                name_part = f'_{safe_name}' if safe_name and safe_name.upper() != emp_id.upper() else ''
                seq_label = f'_{eval_sequence}th' if eval_sequence else ''
                filename  = f"{emp_id.upper()}{name_part}{seq_label}_annual_eval.pdf"

                upload_result = upload_report_to_eval_folder(
                    emp_id.upper(), teacher_name, filename, pdf_bytes,
                    bv_url_map=bv_url_map)

                db.collection(COL_NHR_ANNUAL_EVAL).document(doc_id).update({
                    'report_url':        upload_result['file_url'],
                    'folder_url':        upload_result['folder_url'],
                    'report_updated_at': kst_now(),
                    'report_updated_by': _admin_email(),
                })
                try:
                    from app.services.audit_service import log_audit
                    log_audit(
                        action='annual_eval_report_generated',
                        actor=_admin_email(),
                        target=emp_id.upper(),
                        details={
                            'eval_deadline': eval_deadline,
                            'eval_sequence': eval_sequence,
                            'filename':      filename,
                            'report_url':    upload_result['file_url'],
                            'bulk':          True,
                        },
                        category='eval',
                    )
                except Exception:
                    logger.exception('bulk audit_log failed [%s]', emp_id)
                generated += 1
                results.append({'emp_id': emp_id, 'status': 'SUCCESS', 'filename': filename})
            except Exception as e:
                logger.exception('bulk-generate annual error [%s]: %s', emp_id, e)
                results.append({'emp_id': emp_id, 'status': 'ERROR', 'message': str(e)[:100]})

        cache.delete('ae_list_base_data')
        return success({'generated': generated, 'total': len(docs), 'results': results})
    except Exception:
        logger.exception('api_annual_eval_bulk_generate error')
        return error('An internal error occurred.', 500)
