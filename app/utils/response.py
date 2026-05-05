"""표준 JSON 응답 헬퍼.

모든 API 블루프린트에서 일관된 응답 형식을 사용하기 위해 이 모듈을 import한다.

사용 예:
    from app.utils.response import success, error

    return success({'items': data})
    return error('Not found.', 404)

주의: auth_bp (/api/auth/*) 는 status='OK' 를 사용하므로 이 헬퍼를 사용하지 않는다.
      다른 모든 블루프린트는 status='SUCCESS' / status='ERROR' 를 사용한다.
"""
from flask import jsonify


def success(data: dict | None = None, **kwargs):
    """status='SUCCESS' JSON 응답. HTTP 200.

    Args:
        data: 응답 payload dict. None 이면 빈 응답.
        **kwargs: 추가 최상위 필드.
    """
    body = {'status': 'SUCCESS'}
    if data:
        body.update(data)
    if kwargs:
        body.update(kwargs)
    return jsonify(body), 200


def error(message: str, status_code: int = 400, **kwargs):
    """status='ERROR' JSON 응답.

    Args:
        message: 사용자에게 보여줄 오류 메시지.
        status_code: HTTP 상태 코드 (기본 400).
        **kwargs: 추가 최상위 필드.
    """
    body = {'status': 'ERROR', 'message': message}
    if kwargs:
        body.update(kwargs)
    return jsonify(body), status_code


def unauthorized(message: str = 'Unauthorized.'):
    return error(message, 401)


def forbidden(message: str = 'Permission denied.'):
    return error(message, 403)


def not_found(message: str = 'Not found.'):
    return error(message, 404)


def internal(message: str = 'An internal error occurred.'):
    return error(message, 500)
