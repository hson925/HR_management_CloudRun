from flask import render_template, session
from app.auth_utils import admin_required, role_required
from app.eval_v2.questions import EVAL_TYPE_LABELS
from app.services.user_service import get_user_by_emp_id, get_user_by_email
from app.services.audit_service import log_audit

# Re-export blueprints so main.py can do:
#   from app.eval_v2.routes import eval_v2_bp, eval_v2_api
from app.eval_v2.blueprints import eval_v2_bp, eval_v2_api  # noqa: F401


def build_portal_me():
    """현재 session 기반 portal user dict. emp_id → admin_email 순서로 lookup,
    miss 시 session raw 값 fallback. form.html / my_tasks.html 의 PORTAL_ME inject 용."""
    me = None
    sess_emp = str(session.get('emp_id', '')).strip().lower()
    if sess_emp:
        me = get_user_by_emp_id(sess_emp)
    if not me:
        sess_email = str(session.get('admin_email', '')).strip().lower()
        if sess_email:
            me = get_user_by_email(sess_email)
    return {
        'emp_id': (me or {}).get('emp_id', '') or sess_emp,
        'name':   (me or {}).get('name', '')   or session.get('name', ''),
        'role':   (me or {}).get('role', '')   or session.get('admin_code', ''),
        'campus': (me or {}).get('campus', '') or session.get('campus', ''),
    }


# ── 페이지 라우트 ─────────────────────────────────────────────────────────────
@eval_v2_bp.route('/')
@eval_v2_bp.route('/form')
@role_required('admin', 'MASTER', 'GS', 'TL', 'STL')
def form_page():
    return render_template('eval_v2/form.html', portal_me=build_portal_me())

@eval_v2_bp.route('/admin')
@admin_required
def admin_page():
    return render_template('eval_v2/admin.html',
                           eval_types=EVAL_TYPE_LABELS,
                           admin_email=session.get('admin_email', ''))

@eval_v2_bp.route('/status')
@admin_required
def status_page():
    return render_template('eval_v2/status.html',
                           eval_types=EVAL_TYPE_LABELS)

@eval_v2_bp.route('/sub-assignment')
@admin_required
def sub_assignment_page():
    return render_template('eval_v2/sub_assignment.html')

@eval_v2_bp.route('/annual-eval')
@admin_required
def annual_eval_page():
    return render_template('eval_v2/annual_eval.html')

@eval_v2_bp.route('/analysis')
@admin_required
def analysis_page():
    return render_template('eval_v2/analysis.html')

@eval_v2_bp.route('/my-tasks')
@role_required('admin', 'MASTER', 'GS', 'TL', 'STL')
def my_tasks_page():
    """GS/TL/STL/admin 전용: 본인이 평가해야 할 직원 리스트 + 클릭 → 평가 form 진입.
    admin/MASTER 는 view-as 모드로 다른 campus·role 시점 미리 보기 가능."""
    portal_me = build_portal_me()
    is_admin = portal_me['role'] in ('admin', 'MASTER')
    return render_template('eval_v2/my_tasks.html',
                           portal_me=portal_me,
                           is_admin=is_admin,
                           eval_types=EVAL_TYPE_LABELS)


@eval_v2_bp.route('/campus-status')
@role_required('GS', 'TL')
def campus_status_page():
    """GS/TL 전용: 본인 캠퍼스 피평가자 제출 현황만 표시."""
    emp_id = str(session.get('emp_id', '')).strip().lower()
    my_campus = ''
    me = None
    if emp_id:
        me = get_user_by_emp_id(emp_id)
    if not me:
        email = str(session.get('admin_email', '')).strip().lower()
        if email:
            me = get_user_by_email(email)
    my_campus = ((me or {}).get('campus') or '').strip()
    if my_campus:
        try:
            log_audit('eval_campus_status_view', session.get('admin_email', ''),
                      target=my_campus, category='eval')
        except Exception:
            pass
    return render_template('eval_v2/campus_status.html',
                           eval_types=EVAL_TYPE_LABELS,
                           my_campus=my_campus)


# ── Import API submodules to trigger route registration ───────────────────────
import app.eval_v2.api.sub_ctl        # noqa: F401, E402
import app.eval_v2.api.config         # noqa: F401, E402
import app.eval_v2.api.responses      # noqa: F401, E402
import app.eval_v2.api.sessions       # noqa: F401, E402
import app.eval_v2.api.reports        # noqa: F401, E402
import app.eval_v2.api.notifications  # noqa: F401, E402
import app.eval_v2.api.users          # noqa: F401, E402
import app.eval_v2.api.annual_eval    # noqa: F401, E402
import app.eval_v2.api.drafts         # noqa: F401, E402
import app.eval_v2.api.analysis       # noqa: F401, E402
import app.eval_v2.api.passcode       # noqa: F401, E402
import app.eval_v2.api.my_tasks       # noqa: F401, E402
