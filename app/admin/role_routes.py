"""
app/admin/role_routes.py
/admin/roles 페이지 + 4 API endpoint.

system role 7개 잠금 (이름·삭제·수정 불가). custom role 추가 + label 수정만 허용.
권한 자동 부여 미지원 — custom role 사용자는 모든 보호 라우트 access_denied (의도).
"""
import logging
from flask import Blueprint, render_template, request, session

from app.auth_utils import admin_required, api_admin_required
from app.utils.response import success, error
from app.services import role_service
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/admin/roles')
@admin_required
def admin_roles_page():
    return render_template('admin/roles.html')


@admin_bp.route('/api/v2/admin/roles', methods=['GET'])
@api_admin_required
def api_admin_list_roles():
    """모든 활성 role (system + custom). UI 표시용.
    `?include_deprecated=1` 시 deprecated 도 포함 (admin "Show legacy" 토글용).
    정렬: is_system desc, deprecated asc (active 먼저), name asc.
    """
    try:
        include_deprecated = request.args.get('include_deprecated', '').strip() in ('1', 'true', 'yes')
        roles = role_service.get_all_roles(include_deprecated=include_deprecated)
        return success({'data': {'roles': roles}})
    except Exception:
        logger.exception('api_admin_list_roles failed')
        return error('Failed to load roles.', 500)


@admin_bp.route('/api/v2/admin/roles', methods=['POST'])
@api_admin_required
def api_admin_create_role():
    """custom role 추가. system 예약어·정규식 위반·중복 시 400/409.
    정규식: ^[a-zA-Z][a-zA-Z0-9_-]{1,30}$
    예약어: admin/MASTER/NET/GS/TL/STL/retired/퇴사/__public__/__all__
    """
    try:
        data = request.get_json(silent=True) or {}
        name = data.get('name', '')
        label = data.get('label', '')
        actor = session.get('admin_email') or session.get('emp_id') or 'unknown'
        try:
            result = role_service.add_role(name, label, actor)
        except ValueError as e:
            msg = str(e)
            # 중복은 409, 그 외는 400
            if 'already exists' in msg:
                return error(msg, 409)
            return error(msg, 400)
        log_audit(
            action='role_create',
            actor=actor,
            target=result['name'],
            details={'label': result['label']},
            category='role',
        )
        return success({'role': result})
    except Exception:
        logger.exception('api_admin_create_role failed')
        return error('Failed to create role.', 500)


@admin_bp.route('/api/v2/admin/roles/<name>/update-label', methods=['POST'])
@api_admin_required
def api_admin_update_role_label(name):
    """role 의 label 수정. system role 도 허용 (식별자 name 은 변경 불가)."""
    try:
        data = request.get_json(silent=True) or {}
        label = data.get('label', '')
        actor = session.get('admin_email') or session.get('emp_id') or 'unknown'
        try:
            result = role_service.update_role_label(name, label, actor)
        except ValueError as e:
            return error(str(e), 400)
        except LookupError as e:
            return error(str(e), 404)
        log_audit(
            action='role_update_label',
            actor=actor,
            target=result['name'],
            details={'label': result['label'], 'is_system': result.get('is_system', False)},
            category='role',
        )
        return success({'role': result})
    except Exception:
        logger.exception('api_admin_update_role_label failed')
        return error('Failed to update role.', 500)


@admin_bp.route('/api/v2/admin/roles/<name>/deprecate', methods=['POST'])
@api_admin_required
def api_admin_set_role_deprecated(name):
    """role 의 deprecated 플래그 토글. system + custom 모두 가능.
    body: { "deprecated": bool }
    audit: action='role_deprecate' (true) / 'role_restore' (false)
    """
    try:
        data = request.get_json(silent=True) or {}
        if 'deprecated' not in data:
            return error('deprecated field is required.', 400)
        deprecated = bool(data.get('deprecated'))
        actor = session.get('admin_email') or session.get('emp_id') or 'unknown'
        try:
            result = role_service.set_role_deprecated(name, deprecated, actor)
        except ValueError as e:
            return error(str(e), 400)
        except LookupError as e:
            return error(str(e), 404)
        log_audit(
            action='role_deprecate' if deprecated else 'role_restore',
            actor=actor,
            target=result['name'],
            details={'deprecated': deprecated, 'is_system': result.get('is_system', False)},
            category='role',
        )
        return success({'role': result})
    except Exception:
        logger.exception('api_admin_set_role_deprecated failed')
        return error('Failed to update role.', 500)


@admin_bp.route('/api/v2/admin/roles/<name>/user-count', methods=['GET'])
@api_admin_required
def api_admin_role_user_count(name):
    """role 보유자 수 조회 — 삭제 전 미리보기용."""
    try:
        count = role_service.count_users_with_role(name)
        return success({'data': {'count': count}})
    except ValueError as e:
        return error(str(e), 400)
    except Exception:
        logger.exception('api_admin_role_user_count failed')
        return error('Failed to count users.', 500)


@admin_bp.route('/api/v2/admin/roles/<name>', methods=['DELETE'])
@api_admin_required
def api_admin_delete_role(name):
    """custom role 영구 삭제. 보유자 1+ 또는 system role 시 거부.
    409: role still assigned / 403: system role / 404: not found / 400: invalid name.
    """
    actor = session.get('admin_email') or session.get('emp_id') or 'unknown'
    try:
        role_service.delete_role(name)
    except ValueError as e:
        return error(str(e), 400)
    except LookupError as e:
        return error(str(e), 404)
    except PermissionError as e:
        return error(str(e), 403)
    except RuntimeError as e:
        return error(str(e), 409)
    except Exception:
        logger.exception('api_admin_delete_role failed')
        return error('Failed to delete role.', 500)
    log_audit(
        action='role_delete',
        actor=actor,
        target=name,
        category='role',
    )
    return success()
