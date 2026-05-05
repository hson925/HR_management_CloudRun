import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Blueprint, render_template, jsonify, request, session
from google.cloud import firestore

from app.auth_utils import admin_required, api_admin_required
from app.constants import COL_PORTAL_USERS, RETIRED_ROLES
from app.extensions import limiter, cache
from app.notifications.service import create_bulk_notifications
from app.services.audit_service import log_audit
from app.services.firebase_service import get_firestore_client
from app.utils.html_sanitizer import sanitize_html, strip_to_text
from app.utils.rate_limit import admin_rate_key

logger = logging.getLogger(__name__)

legal_bp = Blueprint('legal', __name__)

# 알림 발송을 백그라운드로 위임 — 저장 응답 속도 개선 + 실패 격리.
# max_workers=2: 동시 두 문서 저장에도 충분, idle 시 thread 유지 부담 작음.
_legal_notify_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='legal-notify')


_VALID_DOCS = {'privacy_policy', 'terms_of_service'}

_MAX_ARTICLES = 50
_MAX_TITLE_LEN = 300
_MAX_CONTENT_LEN = 50_000

_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _validate_effective_date(raw):
    """시행일을 YYYY-MM-DD 로 정규화. 유효하지 않으면 빈 문자열."""
    if not raw:
        return ''
    s = str(raw)[:32].split('T')[0].strip()
    if not _ISO_DATE_RE.match(s):
        return ''
    try:
        datetime.strptime(s, '%Y-%m-%d')
        return s
    except ValueError:
        return ''


def _sanitize_article(article):
    if not isinstance(article, dict):
        return None
    # Title: strip ALL HTML (plain text only)
    title = strip_to_text(article.get('title', ''), max_len=_MAX_TITLE_LEN)
    content = sanitize_html(
        article.get('content', ''),
        max_len=_MAX_CONTENT_LEN,
        allow_img=False,
    )
    # English translations (optional) — 번역이 없으면 빈 문자열. 클라이언트는
    # 빈 값이면 한국어로 fallback.
    title_en = strip_to_text(article.get('title_en', ''), max_len=_MAX_TITLE_LEN)
    content_en = sanitize_html(
        article.get('content_en', ''),
        max_len=_MAX_CONTENT_LEN,
        allow_img=False,
    )
    try:
        number = int(article.get('number', 0))
    except (TypeError, ValueError):
        number = 0
    # 범위 밖 값은 일단 0으로 두고, 이후 _normalize_articles 에서 재부여
    if number < 1 or number > _MAX_ARTICLES:
        number = 0
    # 소속 장 정보 (optional, 한/영)
    try:
        chapter_number = int(article.get('chapter_number') or 0)
    except (TypeError, ValueError):
        chapter_number = 0
    if chapter_number < 0 or chapter_number > _MAX_ARTICLES:
        chapter_number = 0
    chapter_title = strip_to_text(article.get('chapter_title', ''), max_len=_MAX_TITLE_LEN)
    chapter_title_en = strip_to_text(article.get('chapter_title_en', ''), max_len=_MAX_TITLE_LEN)
    # W3: AI 번역 감사 메타. ai_translated_at 은 클라이언트가 Apply 시 ISO
    # 타임스탬프 주입. 값이 있으면 사이드바에 ⚡ 아이콘 표시 → 검수 대상 식별.
    # 길이 32자 내 ISO 형식만 허용 (injection 방지). 비면 빈 문자열.
    ai_translated_at_raw = str(article.get('ai_translated_at', '') or '').strip()[:32]
    if ai_translated_at_raw and not re.match(r'^\d{4}-\d{2}-\d{2}T[\d:.+Z\-]+$', ai_translated_at_raw):
        ai_translated_at_raw = ''
    return {
        'number': number,
        'title': title,
        'content': content,
        'title_en': title_en,
        'content_en': content_en,
        'chapter_number': chapter_number,
        'chapter_title': chapter_title,
        'chapter_title_en': chapter_title_en,
        'ai_translated_at': ai_translated_at_raw,
    }


def _normalize_articles(raw_articles):
    """살균된 article 들을 순서대로 정렬하고 1..N 으로 재번호 부여."""
    cleaned = [a for a in (_sanitize_article(x) for x in raw_articles) if a is not None]
    # 유효 번호가 있는 항목을 먼저 정렬 기준으로 사용 (0은 맨 뒤)
    cleaned.sort(key=lambda a: (a['number'] == 0, a['number']))
    for i, a in enumerate(cleaned, start=1):
        a['number'] = i
    return cleaned

_DOC_META = {
    'privacy_policy':  {'title': '개인정보 처리 방침', 'en': 'Privacy Policy'},
    'terms_of_service': {'title': '이용약관',           'en': 'Terms of Service'},
}


# ── Public pages ──────────────────────────────────────────────

@legal_bp.route('/privacy')
def privacy_page():
    doc = _get_doc('privacy_policy')
    return render_template('legal/public.html',
                           doc=doc, doc_id='privacy_policy',
                           meta=_DOC_META['privacy_policy'])


@legal_bp.route('/terms')
def terms_page():
    doc = _get_doc('terms_of_service')
    return render_template('legal/public.html',
                           doc=doc, doc_id='terms_of_service',
                           meta=_DOC_META['terms_of_service'])


# ── Master-only editor pages ───────────────────────────────────

@legal_bp.route('/admin/legal/privacy')
@admin_required
def edit_privacy():
    doc = _get_doc('privacy_policy')
    log_audit('legal_doc_view_editor',
              actor=session.get('admin_email', ''),
              target='privacy_policy', category='general')
    return render_template('legal/editor.html',
                           doc=doc, doc_id='privacy_policy',
                           meta=_DOC_META['privacy_policy'])


@legal_bp.route('/admin/legal/terms')
@admin_required
def edit_terms():
    doc = _get_doc('terms_of_service')
    log_audit('legal_doc_view_editor',
              actor=session.get('admin_email', ''),
              target='terms_of_service', category='general')
    return render_template('legal/editor.html',
                           doc=doc, doc_id='terms_of_service',
                           meta=_DOC_META['terms_of_service'])


# ── Save API ──────────────────────────────────────────────────

class _LegalConflict(Exception):
    def __init__(self, current_version):
        self.current_version = current_version


@legal_bp.route('/api/legal/save/<doc_id>', methods=['POST'])
@api_admin_required
@limiter.limit("5 per minute", key_func=admin_rate_key)
def api_save_legal(doc_id):
    if doc_id not in _VALID_DOCS:
        return jsonify({'status': 'ERROR', 'message': 'Invalid document ID.'})

    if not request.is_json:
        return jsonify({'status': 'ERROR', 'message': 'JSON body required.'}), 400

    actor = session.get('admin_email', '')
    if not actor:
        return jsonify({'status': 'ERROR', 'message': 'Session error.'}), 401

    try:
        data = request.get_json(silent=True) or {}
        raw_articles   = data.get('articles', [])
        effective_date = _validate_effective_date(data.get('effective_date', ''))
        major_revision = bool(data.get('major_revision', False))

        try:
            client_version = int(data.get('version', 0))
        except (TypeError, ValueError):
            client_version = 0

        if not isinstance(raw_articles, list):
            return jsonify({'status': 'ERROR', 'message': 'Invalid articles format.'})
        if len(raw_articles) > _MAX_ARTICLES:
            return jsonify({'status': 'ERROR', 'message': f'Too many articles (max {_MAX_ARTICLES}).'})

        articles = _normalize_articles(raw_articles)

        db  = get_firestore_client()
        ref = db.collection('legal_docs').document(doc_id)

        @firestore.transactional
        def _txn_save(tx):
            snap = ref.get(transaction=tx)
            current = 0
            if snap.exists:
                current = int((snap.to_dict() or {}).get('version', 0) or 0)
            if client_version != current:
                raise _LegalConflict(current)
            next_version = current + 1
            payload = {
                'articles':       articles,
                'effective_date': effective_date,
                'version':        next_version,
                'updated_at':     datetime.now(timezone.utc).isoformat(),
                'updated_by':     actor,
            }
            # 중대 개정 시 force_agreement_version 을 새 버전으로 올려 기존
            # 동의자에게도 재동의 강제. tos-status 가 agreed < force 를 보고
            # needs_agreement=True 반환. 일반 개정에는 이 필드 건드리지 않음.
            if major_revision:
                payload['force_agreement_version'] = next_version
                payload['last_major_revision_at'] = datetime.now(timezone.utc).isoformat()
            tx.set(ref, payload, merge=True)
            return next_version

        try:
            new_version = _txn_save(db.transaction())
        except _LegalConflict as conflict:
            return jsonify({
                'status': 'CONFLICT',
                'message': '다른 사용자가 이미 문서를 수정했습니다. 새로고침 후 다시 시도해주세요.',
                'current_version': conflict.current_version,
            }), 409

        log_audit('legal_doc_update',
                  actor=actor,
                  target=doc_id,
                  details={
                      'version': new_version,
                      'articles_count': len(articles),
                      'major_revision': major_revision,
                  },
                  category='general')

        # 약관 개정 알림 — 기존 동의자에게만 우측 상단 🔔 벨 알림. 신규 유저는
        # 다음 로그인 시 팝업이 담당. 백그라운드 스레드로 위임 — 저장 응답 속도
        # 개선 + 실패가 저장 트랜잭션에 영향 안 줌. portal_users 전체 스캔은
        # ~150명 규모에선 빠르지만 응답 경로에서 제외하는 편이 안전.
        notified_count_future = _legal_notify_pool.submit(
            _notify_users_of_legal_update, doc_id, new_version, major_revision,
        )
        # 응답에 포함할 발송 수 — 타임아웃 짧게 (2s). 초과 시 -1 (알 수 없음).
        try:
            notified = notified_count_future.result(timeout=2.0)
        except Exception:
            notified = -1

        return jsonify({
            'status': 'SUCCESS',
            'version': new_version,
            'major_revision': major_revision,
            'notified': notified,
        })
    except Exception:
        logger.exception('api_save_legal error for doc_id=%s', doc_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Helper ────────────────────────────────────────────────────

def _tl_cache_key(title_ko: str, content_ko: str, model: str = 'gpt-4o') -> str:
    """번역 결과 캐시 키 — 입력이 정확히 같으면 OpenAI 재호출 없이 재사용.
    SHA-256 으로 해시해 키 길이 고정 + 민감 콘텐츠가 캐시 키에 노출되지 않도록.
    """
    h = hashlib.sha256()
    h.update((model + '\x00').encode('utf-8'))
    h.update((title_ko + '\x00').encode('utf-8'))
    h.update(content_ko.encode('utf-8'))
    return f'legal_tl:{h.hexdigest()}'


def _translate_with_cache(title_ko: str, content_ko: str, model: str = 'gpt-4o') -> dict:
    """translate_legal_article 의 캐시 래퍼.
    성공한 번역만 24h 캐시 — 실패(Exception) 는 캐시하지 않아 재시도 가능.
    """
    from app.services.openai_service import translate_legal_article
    key = _tl_cache_key(title_ko, content_ko, model)
    cached = cache.get(key)
    if cached is not None:
        # 캐시 히트는 input/output_tokens=0 으로 기록 — 실제 API 호출 없음 표시.
        return dict(cached, input_tokens=0, output_tokens=0, cached=True)
    result = translate_legal_article(title_ko, content_ko, model=model)
    cache.set(key, result, timeout=24 * 3600)
    result['cached'] = False
    return result


@legal_bp.route('/api/legal/translate-article/<doc_id>', methods=['POST'])
@api_admin_required
@limiter.limit('10 per minute', key_func=admin_rate_key)
def api_translate_article(doc_id):
    """단일 조항 한국어 → 영어 초안 번역. 저장은 하지 않고 번역 결과만 반환 —
    클라이언트가 미리보기 후 Apply 시 기존 save API 로 저장.

    요청: {title_ko: str, content_ko_html: str}
    반환: {status: 'SUCCESS', title_en, content_en_html, model, input_tokens, output_tokens}
    """
    if doc_id not in _VALID_DOCS:
        return jsonify({'status': 'ERROR', 'message': 'Invalid document ID.'}), 400

    actor = session.get('admin_email', '') or 'unknown'
    data = request.get_json(silent=True) or {}
    title_ko = str(data.get('title_ko') or '').strip()
    content_ko_html = str(data.get('content_ko_html') or '').strip()

    if not title_ko and not content_ko_html:
        return jsonify({'status': 'ERROR', 'message': 'Both title and content are empty — nothing to translate.'}), 400
    if len(title_ko) > _MAX_TITLE_LEN or len(content_ko_html) > _MAX_CONTENT_LEN:
        return jsonify({'status': 'ERROR', 'message': 'Input exceeds size limits.'}), 400

    article_number = data.get('article_number')
    try:
        article_number = int(article_number) if article_number is not None else None
    except (TypeError, ValueError):
        article_number = None

    from app.services.openai_service import LegalTranslateError
    try:
        result = _translate_with_cache(title_ko, content_ko_html, model='gpt-4o')
    except LegalTranslateError as e:
        return jsonify({'status': 'ERROR', 'message': str(e)}), e.http_status
    except Exception:
        logger.exception('api_translate_article unexpected error')
        return jsonify({'status': 'ERROR', 'message': 'Unexpected translation failure.'}), 500

    # 감사 로그 — 번역 호출 추적 (법적으로 "이 번역은 AI 생성" 입증용)
    try:
        log_audit('legal_article_translated', actor=actor, target=doc_id,
                  details={
                      'article_number':  article_number,
                      'model':           result.get('model'),
                      'input_tokens':    result.get('input_tokens'),
                      'output_tokens':   result.get('output_tokens'),
                      'title_ko_len':    len(title_ko),
                      'content_ko_len':  len(content_ko_html),
                  },
                  category='general')
    except Exception:
        logger.exception('log_audit failed for legal_article_translated')

    return jsonify({
        'status':          'SUCCESS',
        'title_en':        result['title_en'],
        'content_en_html': result['content_en_html'],
        'model':           result['model'],
        'input_tokens':    result['input_tokens'],
        'output_tokens':   result['output_tokens'],
    })


@legal_bp.route('/api/legal/translate-missing/<doc_id>', methods=['POST'])
@api_admin_required
@limiter.limit('3 per minute', key_func=admin_rate_key)
def api_translate_missing(doc_id):
    """영어 번역이 비어있는 조항들만 일괄 번역. 기존 영어 번역은 보존.

    요청: {articles: [{number, title, content, title_en, content_en}, ...]}
          — 클라이언트가 에디터 현재 상태 그대로 전달 (미저장 내용 포함).
    반환: {status, translations: [{number, title_en, content_en_html, ...}],
           skipped: [{number, reason}], total_input_tokens, total_output_tokens}

    저장은 하지 않음 — 클라이언트가 미리보기 후 Apply 시 반영.
    완전 번역 (title_en AND content_en 모두 존재) 인 조항은 skip.
    """
    if doc_id not in _VALID_DOCS:
        return jsonify({'status': 'ERROR', 'message': 'Invalid document ID.'}), 400

    actor = session.get('admin_email', '') or 'unknown'
    data = request.get_json(silent=True) or {}
    articles = data.get('articles', [])
    if not isinstance(articles, list) or not articles:
        return jsonify({'status': 'ERROR', 'message': 'articles list required.'}), 400
    if len(articles) > _MAX_ARTICLES:
        return jsonify({'status': 'ERROR', 'message': f'Too many articles (max {_MAX_ARTICLES}).'}), 400

    from app.services.openai_service import LegalTranslateError
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Phase 1: validate + filter — 번역 대상 · skip · errors 분류.
    # OpenAI 호출 전에 per-article 크기 체크로 비용 공격 방어 (M1).
    targets = []  # [(number, title_ko, content_ko)]
    skipped = []
    errors = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        try:
            number = int(a.get('number', 0))
        except (TypeError, ValueError):
            number = 0
        title_ko = str(a.get('title') or '').strip()
        content_ko = str(a.get('content') or '').strip()
        title_en_existing = str(a.get('title_en') or '').strip()
        content_en_existing = str(a.get('content_en') or '').strip()

        # 완전 번역되어있으면 skip
        if title_en_existing and content_en_existing:
            skipped.append({'number': number, 'reason': 'already_translated'})
            continue

        # 번역할 한국어 콘텐츠가 전혀 없으면 skip
        if not title_ko and not content_ko:
            skipped.append({'number': number, 'reason': 'empty_korean'})
            continue

        # per-article 크기 검증 — 공격 방어 + 비용 통제
        if len(title_ko) > _MAX_TITLE_LEN or len(content_ko) > _MAX_CONTENT_LEN:
            errors.append({'number': number, 'reason': 'exceeds_size_limits'})
            continue

        targets.append((number, title_ko, content_ko))

    translations = []
    total_in = 0
    total_out = 0
    cached_hits = 0

    # Phase 2: 병렬 번역 — ThreadPoolExecutor max_workers=3 (M2, N1).
    # 이전 max_workers=5 는 OpenAI tier-1 RPM/TPM 에 한순간 몰려 429 연쇄 실패
    # 위험. 3으로 낮춰 RPM 여유 확보. 429 수신 시 exponential backoff (1회 재시도).
    # 예기치 않은 Exception 도 errors 로 수렴 — with 블록이 pool 을 정상 종료 (N3).
    def _call(args):
        number, title_ko, content_ko = args
        for attempt in range(2):  # 첫 시도 + 429 재시도 1회
            try:
                result = _translate_with_cache(title_ko, content_ko, model='gpt-4o')
                return ('ok', number, result)
            except LegalTranslateError as e:
                # 429 Too Many Requests — short backoff 후 1회 재시도
                if getattr(e, 'http_status', 0) == 429 and attempt == 0:
                    time.sleep(1.5)
                    continue
                logger.warning('batch translate: article %s failed: %s', number, e)
                return ('err', number, str(e))
            except Exception:
                # 예기치 않은 (예: 메모리·네트워크·JSON 파싱) 에러도 수렴
                logger.exception('batch translate: article %s unexpected error', number)
                return ('err', number, 'Unexpected failure')
        return ('err', number, 'Rate limited (retries exhausted)')

    if targets:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_call, t) for t in targets]
            for fut in as_completed(futures):
                kind, number, payload = fut.result()
                if kind == 'err':
                    errors.append({'number': number, 'reason': payload})
                    continue
                r = payload
                if r.get('cached'):
                    cached_hits += 1
                translations.append({
                    'number':          number,
                    'title_en':        r['title_en'],
                    'content_en_html': r['content_en_html'],
                    'model':           r['model'],
                    'input_tokens':    r['input_tokens'],
                    'output_tokens':   r['output_tokens'],
                    'cached':          r.get('cached', False),
                })
                total_in  += r['input_tokens']
                total_out += r['output_tokens']
    translations.sort(key=lambda x: x['number'])

    try:
        log_audit('legal_articles_batch_translated', actor=actor, target=doc_id,
                  details={
                      'translated_count':  len(translations),
                      'skipped_count':     len(skipped),
                      'error_count':       len(errors),
                      'cached_hits':       cached_hits,
                      'total_input_tokens':  total_in,
                      'total_output_tokens': total_out,
                      'model':             'gpt-4o',
                  },
                  category='general')
    except Exception:
        logger.exception('log_audit failed for legal_articles_batch_translated')

    return jsonify({
        'status':               'SUCCESS',
        'translations':         translations,
        'skipped':              skipped,
        'errors':               errors,
        'total_input_tokens':   total_in,
        'total_output_tokens':  total_out,
    })


def _get_doc(doc_id):
    try:
        db  = get_firestore_client()
        ref = db.collection('legal_docs').document(doc_id).get()
        if ref.exists:
            data = ref.to_dict() or {}
            data.setdefault('version', 0)
            return data
    except Exception:
        logger.exception('_get_doc(%s) error', doc_id)
    return {'articles': [], 'effective_date': '', 'updated_at': '', 'version': 0}


# ── 약관 개정 알림 ─────────────────────────────────────────────

_DOC_LABEL_KO = {
    'privacy_policy':   '개인정보 처리방침',
    'terms_of_service': '이용약관',
}
_DOC_LABEL_EN = {
    'privacy_policy':   'Privacy Policy',
    'terms_of_service': 'Terms of Service',
}
_DOC_LINK = {
    'privacy_policy':   '/privacy',
    'terms_of_service': '/terms',
}


def _notify_users_of_legal_update(doc_id: str, new_version: int,
                                  major_revision: bool = False) -> int:
    """약관/개인정보 개정 시 **기존 유저** 에게만 알림 발송.

    제외 대상:
    - 퇴직자 (`role in RETIRED_ROLES`)
    - 신규 유저 (`agreed_privacy_version`/`agreed_terms_version` 둘 다 없거나 0).
      이들은 로그인 시점에 `tosAgreeModal` 팝업으로 최초 동의하게 됨 —
      알림을 보내면 UX 중복. `auth/routes.py:api_tos_status` 가 first-time
      판정 책임을 진다.

    Args:
      major_revision: True 면 알림 메시지를 "재동의 필요" 톤으로 전환.
        이 경우 `api_save_legal` 이 doc 의 `force_agreement_version` 을 올렸
        으므로 기존 동의자도 다음 로그인 시 팝업이 뜬다.

    중복 이메일 제거. create_bulk_notifications 가 400-doc 배치 commit 수행.
    """
    if doc_id not in _DOC_LABEL_KO:
        return 0
    db = get_firestore_client()
    emails = []
    try:
        for doc in db.collection(COL_PORTAL_USERS).stream():
            d = doc.to_dict() or {}
            if d.get('role') in RETIRED_ROLES:
                continue
            # 신규 유저 (한 번도 동의 안 함) 은 팝업이 담당 — 알림 제외.
            has_prior = (
                int(d.get('agreed_privacy_version', 0) or 0) >= 1
                or int(d.get('agreed_terms_version', 0) or 0) >= 1
            )
            if not has_prior:
                continue
            email = (d.get('email') or '').strip().lower()
            if email:
                emails.append(email)
    except Exception:
        logger.exception('_notify_users_of_legal_update: portal_users 조회 실패')
        return 0

    if not emails:
        return 0

    label_ko = _DOC_LABEL_KO[doc_id]
    label_en = _DOC_LABEL_EN[doc_id]
    link = _DOC_LINK[doc_id]
    if major_revision:
        title = f'{label_ko} 중대 개정 — 재동의 필요 · {label_en} major revision'
        message = (
            f'{label_ko} 이(가) 버전 {new_version} 으로 중대 개정되었습니다. '
            f'다음 로그인 시 재동의 팝업이 표시됩니다.\n'
            f'{label_en} has been significantly updated to version {new_version}. '
            f'Re-agreement will be required on your next login.'
        )
    else:
        title = f'{label_ko} 개정 안내 · {label_en} updated'
        message = (
            f'{label_ko} 이(가) 버전 {new_version} 으로 개정되었습니다. '
            f'내용을 확인해 주세요.\n'
            f'{label_en} has been updated to version {new_version}. '
            f'Please review the changes.'
        )

    sent = create_bulk_notifications(
        user_emails=emails,
        ntype='system',
        title=title,
        message=message,
        link=link,
    )
    logger.info('legal update notifications sent: doc=%s version=%d users=%d',
                doc_id, new_version, sent)
    return sent
