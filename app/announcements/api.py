"""공지사항 API 엔드포인트 — 모든 /api/announcements/* 라우트."""
import logging
import re
import uuid

from flask import jsonify, request, session
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.auth_utils import api_admin_required
from app.constants import ADMIN_ROLES
from app.extensions import limiter
from app.services.audit_service import log_audit
from app.services.firebase_service import get_firestore_client
from app.services.user_service import get_user_by_email
from app.utils.html_sanitizer import sanitize_html, strip_to_text
from app.utils.storage import (
    StorageDisabled, UploadRejected,
    delete_prefix, is_storage_enabled,
    upload_attachment, upload_image,
)
from app.utils.youtube import normalize_youtube_urls
from app.announcements.routes import (
    announcements_bp, _user_rate_key,
    _now_iso, _session_role, _is_admin,
)
from app.utils.rate_limit import admin_rate_key
from app.announcements.service import (
    _ALL_SENTINEL, _SELECTABLE_ROLES,
    _MAX_TITLE_LEN, _MAX_CONTENT_LEN,
    _MAX_YT, _MAX_IMAGES, _MAX_ATTACHMENTS, _MAX_COMMENT_LEN,
    _MAX_POLL_OPTIONS, _MAX_POLL_OPTION_LEN, _MAX_POLL_QUESTION_LEN,
    _POLL_OPT_ID_RE, _can_read, _normalize_allowed_roles, _validate_refs_multi,
    _doc_to_summary, _voter_key, _is_poll_ended, _is_new,
    _reaction_key, _reaction_count, _AnnouncementConflict,
)

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_notif_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='announcement_notif')

_LIST_DEFAULT_LIMIT = 20
_LIST_MAX_LIMIT = 200


# ── Private helpers ───────────────────────────────────────

def _load_post_for_user(post_id):
    db = get_firestore_client()
    ref = db.collection('announcements').document(post_id)
    snap = ref.get()
    if not snap.exists:
        return None, None
    data = snap.to_dict() or {}
    if data.get('status') != 'published' and not _is_admin():
        return None, None
    if not _can_read(data, _session_role()):
        return None, None
    return ref, data


def _last_read_doc(email):
    db = get_firestore_client()
    return db.collection('announcement_reads').document((email or '').lower().strip())


def _upload_prefix(post_id, kind):
    base = f'announcements/{post_id}' if post_id else 'announcements/_tmp'
    return f'{base}/{kind}'


# ── List API ──────────────────────────────────────────────

@announcements_bp.route('/api/announcements/list')
def api_list():
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        try:
            limit = min(int(request.args.get('limit', _LIST_DEFAULT_LIMIT)), _LIST_MAX_LIMIT)
        except (TypeError, ValueError):
            limit = _LIST_DEFAULT_LIMIT
        _raw_before = (request.args.get('before') or '').strip()[:40]
        before = ''
        if _raw_before:
            try:
                from datetime import datetime
                datetime.fromisoformat(_raw_before.replace('Z', '+00:00'))
                before = _raw_before
            except ValueError:
                pass

        role = _session_role()
        db = get_firestore_client()

        # 단일 필드 정렬(created_at DESC)만 사용 → 자동 인덱스로 동작, 복합 인덱스 배포 불필요.
        # status 필터링·role 필터링·pinned 정렬은 모두 Python 에서 처리.
        # 사내 포털 규모(~수백 건)에서 성능 차이 없음.
        q = (db.collection('announcements')
             .order_by('created_at', direction='DESCENDING'))
        if before:
            q = q.start_after({'created_at': before})
        # 페이지 크기의 여유분 확보 (필터 후 부족할 수 있어 넉넉히 fetch)
        q = q.limit(limit * 4 + 20)

        # to_dict()는 한 번만 호출 후 (id, data) 튜플로 처리
        all_docs_data = [(d.id, d.to_dict() or {}) for d in q.stream()]
        is_admin_user = _is_admin()
        # status + role 기반 필터링 (Python)
        # admin은 draft·hidden도 볼 수 있음
        filtered = [
            (doc_id, data) for doc_id, data in all_docs_data
            if (data.get('status') == 'published'
                or (is_admin_user and data.get('status') in ('draft', 'hidden')))
            and _can_read(data, role)
        ]
        # pinned 우선 정렬: Firestore 는 이미 created_at DESC 로 반환하므로
        # pinned=True 인 글을 앞으로 이동하는 stable sort 만 수행.
        filtered.sort(key=lambda x: not bool(x[1].get('pinned')))

        has_more = len(filtered) > limit
        page = filtered[:limit]

        items = [_doc_to_summary(doc_id, data) for doc_id, data in page]
        next_cursor = items[-1]['created_at'] if (has_more and items) else ''
        return jsonify({
            'status': 'SUCCESS',
            'items': items,
            'next_cursor': next_cursor,
            'has_more': has_more,
        })
    except Exception:
        logger.exception('api_list error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Save API ──────────────────────────────────────────────

@announcements_bp.route('/api/announcements/save', methods=['POST'])
@api_admin_required
@limiter.limit("10 per minute", key_func=admin_rate_key)
def api_save():
    try:
        data = request.get_json(silent=True) or {}
        _raw_post_id = str(data.get('post_id') or '').strip()
        post_id = _raw_post_id if _raw_post_id else None
        # post_id 형식 검증: Firestore doc ID에 '/' 불허, 최대 128자
        if post_id and ('/' in post_id or len(post_id) > 128):
            return jsonify({'status': 'ERROR', 'message': 'Invalid post_id format.'}), 400

        title = strip_to_text(data.get('title', ''), max_len=_MAX_TITLE_LEN)
        if not title:
            return jsonify({'status': 'ERROR', 'message': 'Title is required.'}), 400

        content_html = sanitize_html(
            data.get('content', ''), max_len=_MAX_CONTENT_LEN, allow_img=True,
        )
        # Quill 기본 출력(<p><br></p>)처럼 마크업만 있고 실제 텍스트가 없는 경우도 거부.
        # strip_to_text는 preview용 max_len=200으로 호출하고,
        # 유효성 검사는 전체 HTML에서 태그만 제거한 텍스트로 별도 확인.
        _plain_check = re.sub(r'<[^>]+>', '', content_html).strip()
        if not content_html or not _plain_check:
            return jsonify({'status': 'ERROR', 'message': 'Content is required.'}), 400
        content_text = strip_to_text(content_html, max_len=200)

        youtube_videos = normalize_youtube_urls(
            data.get('youtube_urls', []), limit=_MAX_YT,
        )
        _yt_pos_raw = str(data.get('youtube_position') or 'bottom')
        youtube_position = _yt_pos_raw if _yt_pos_raw in ('top', 'after_content', 'bottom') else 'bottom'

        allowed_roles = _normalize_allowed_roles(data.get('allowed_roles'))
        pinned = bool(data.get('pinned'))

        # Poll configuration
        poll_raw = data.get('poll')
        poll_data = None
        if isinstance(poll_raw, dict) and poll_raw.get('enabled'):
            question = strip_to_text(poll_raw.get('question', ''), max_len=_MAX_POLL_QUESTION_LEN)
            mode = 'anonymous' if poll_raw.get('mode') == 'anonymous' else 'named'
            opts_raw = poll_raw.get('options') or []
            options = []
            seen_labels: set = set()
            for opt in (opts_raw if isinstance(opts_raw, list) else [])[:_MAX_POLL_OPTIONS]:
                if not isinstance(opt, dict):
                    continue
                label = strip_to_text(str(opt.get('label', '')), max_len=_MAX_POLL_OPTION_LEN)
                if not label or label in seen_labels:
                    continue
                seen_labels.add(label)
                raw_id = str(opt.get('id', '')).strip()
                opt_id = raw_id if _POLL_OPT_ID_RE.match(raw_id) else uuid.uuid4().hex[:8]
                options.append({'id': opt_id, 'label': label})
            ends_at_raw = str(poll_raw.get('ends_at', '') or '').strip()
            ends_at = None
            if ends_at_raw:
                try:
                    from datetime import datetime
                    datetime.fromisoformat(ends_at_raw.replace('Z', '+00:00'))
                    ends_at = ends_at_raw
                except ValueError:
                    pass
            if question and len(options) >= 2:
                poll_data = {
                    'enabled': True,
                    'question': question,
                    'mode': mode,
                    'options': options,
                    'ends_at': ends_at,
                }

        # visibility: published | hidden | draft
        publish = bool(data.get('publish', True))
        if not publish:
            save_status = 'draft'
        else:
            _vis = str(data.get('visibility', 'published')).strip()
            save_status = 'hidden' if _vis == 'hidden' else 'published'

        try:
            client_version = int(data.get('version', 0))
        except (TypeError, ValueError):
            client_version = 0

        # Storage refs 검증.
        # 기존 글 수정: 해당 post_id 경로 또는 _tmp 경로만 허용.
        # 신규 글 작성: _tmp 경로 (에디터가 post_id 없이 임시 업로드).
        if post_id:
            _allowed_img_prefix = (f'announcements/{post_id}/images/', 'announcements/_tmp/')
            _allowed_att_prefix = (f'announcements/{post_id}/files/', 'announcements/_tmp/')
        else:
            _allowed_img_prefix = ('announcements/_tmp/', 'announcements/')
            _allowed_att_prefix = ('announcements/_tmp/', 'announcements/')

        images = _validate_refs_multi(
            data.get('image_refs', []),
            allowed_prefixes=_allowed_img_prefix,
            max_count=_MAX_IMAGES,
            keys=['url', 'content_type', 'size', 'width', 'height'],
        )
        attachments = _validate_refs_multi(
            data.get('attachment_refs', []),
            allowed_prefixes=_allowed_att_prefix,
            max_count=_MAX_ATTACHMENTS,
            keys=['url', 'filename', 'content_type', 'size'],
        )

        # 캠퍼스 알림 대상 + @멘션
        notify_campuses = []
        raw_campuses = data.get('notify_campuses', [])
        if isinstance(raw_campuses, list):
            notify_campuses = [str(c).strip() for c in raw_campuses if str(c).strip()][:20]
        mentions = []
        raw_mentions = data.get('mentions', [])
        if isinstance(raw_mentions, list):
            for m in raw_mentions[:20]:
                if isinstance(m, dict) and m.get('email'):
                    mentions.append({'email': str(m['email']).strip(), 'name': str(m.get('name','')).strip()})

        # 대상 사용자 (Target Users)
        target_users = []
        raw_targets = data.get('target_users', [])
        if isinstance(raw_targets, list):
            for t in raw_targets[:50]:
                if isinstance(t, dict) and t.get('email'):
                    target_users.append({
                        'email': str(t['email']).strip().lower(),
                        'name': str(t.get('name', '')).strip(),
                    })
        notify_target_email = bool(data.get('notify_target_email', False))

        actor = session.get('admin_email', '')
        actor_name = session.get('emp_name', '') or actor

        db = get_firestore_client()
        col = db.collection('announcements')
        ref = col.document(post_id) if post_id else col.document()
        final_id = ref.id

        now_iso = _now_iso()

        @firestore.transactional
        def _txn(tx):
            snap = ref.get(transaction=tx)
            current = 0
            created_at = now_iso
            author_email = actor
            author_name_val = actor_name
            preserved_views = 0
            edited_at = None
            prev_status = None
            if snap.exists:
                cur = snap.to_dict() or {}
                current = int(cur.get('version', 0) or 0)
                created_at = cur.get('created_at') or now_iso
                author_email = cur.get('author_email') or actor
                author_name_val = cur.get('author_name') or actor_name
                preserved_views = int(cur.get('views', 0) or 0)
                edited_at = now_iso  # 수정 시각 기록
                prev_status = cur.get('status')
            if client_version != current:
                raise _AnnouncementConflict(current)
            next_version = current + 1
            payload = {
                'title':            title,
                'content':          content_html,
                'content_text':     content_text,
                'images':           images,
                'attachments':      attachments,
                'youtube_videos':   youtube_videos,
                'youtube_position': youtube_position,
                'allowed_roles':    allowed_roles,
                'pinned':           pinned,
                'pinned_at':        now_iso if pinned else None,
                'poll':             poll_data,
                'status':           save_status,
                'version':          next_version,
                'author_email':     author_email,
                'author_name':      author_name_val,
                'created_at':       created_at,
                'updated_at':       now_iso,
                'updated_by':       actor,
                'views':            preserved_views,
                'edited_at':        edited_at,
                'notify_campuses':  notify_campuses,
                'mentions':         mentions,
                'target_users':     target_users,
            }
            tx.set(ref, payload)
            return next_version, snap.exists, prev_status

        try:
            new_version, existed, prev_status = _txn(db.transaction())
        except _AnnouncementConflict as c:
            return jsonify({
                'status': 'CONFLICT',
                'message': 'Another user saved first. Please refresh and try again.',
                'current_version': c.current_version,
            }), 409

        log_audit(
            'announcement_update' if existed else 'announcement_create',
            actor=actor,
            target=final_id,
            details={
                'title': title[:80],
                'version': new_version,
                'images': len(images),
                'attachments': len(attachments),
                'youtube': len(youtube_videos),
                'allowed_roles': allowed_roles,
                'pinned': pinned,
            },
            category='announcement',
        )

        # 메인 위젯 캐시 무효화
        try:
            from app.services.cache_service import invalidate_top_announcements
            invalidate_top_announcements()
        except Exception:
            pass

        # 발행 시 인앱 알림 전송 (백그라운드)
        # 조건: 현재 status가 published이고, 이전 status가 published가 아닐 때
        # (신규 발행 OR 초안 → 발행 전환 모두 포함)
        if save_status == 'published' and prev_status != 'published':
            try:
                from app.notifications.service import create_bulk_notifications, create_notification
                from app.services.user_service import get_all_users
                _post_title = title[:80]
                _post_link = f'/announcements/{final_id}'
                _actor_label = actor_name or actor
                _actor_email = actor
                _mentions_copy = list(mentions)
                _campuses_copy = list(notify_campuses)
                _roles_copy = list(allowed_roles)
                _target_users_copy = list(target_users)
                _notify_target_email = notify_target_email
                _final_id = final_id
                def _send_notifs():
                    try:
                        users = get_all_users()
                        # 1) 역할 기반 전체 알림 (작성자 본인 제외)
                        role_emails = []
                        for u in users:
                            if (u.get('role') or '') in ('retired', '퇴사'):
                                continue
                            if u.get('email', '') == _actor_email:
                                continue
                            if '__all__' in _roles_copy or u.get('role', '') in _roles_copy:
                                role_emails.append(u.get('email', ''))
                        if role_emails:
                            create_bulk_notifications(
                                role_emails,
                                ntype='announcement',
                                title=_post_title,
                                message=f'New post by {_actor_label}',
                                link=_post_link,
                            )
                        # 2) @멘션 알림 — 역할 알림을 이미 받은 사람은 mention type으로 추가 발송하지 않음
                        role_email_set = set(role_emails)
                        for m in _mentions_copy:
                            email = m.get('email', '').strip()
                            if email and email != _actor_email and email not in role_email_set:
                                create_notification(
                                    email,
                                    ntype='mention',
                                    title=f'You were mentioned in: {_post_title}',
                                    message=f'Mentioned by {_actor_label}',
                                    link=_post_link,
                                )
                        # 3) 캠퍼스 그룹 알림 (역할 알림 대상과 중복 제거, 작성자 본인 제외)
                        if _campuses_copy:
                            campus_emails = set()
                            for u in users:
                                if (u.get('role') or '') in ('retired', '퇴사'):
                                    continue
                                if u.get('email', '') == _actor_email:
                                    continue
                                if u.get('campus', '') in _campuses_copy:
                                    campus_emails.add(u.get('email', ''))
                            campus_only = campus_emails - role_email_set
                            if campus_only:
                                create_bulk_notifications(
                                    list(campus_only),
                                    ntype='announcement',
                                    title=_post_title,
                                    message=f'New post for your campus by {_actor_label}',
                                    link=_post_link,
                                )
                        # 4) 대상 사용자 개별 알림 (이미 알림 받은 사람 중복 제거)
                        all_notified = role_email_set | {m.get('email','').strip() for m in _mentions_copy}
                        target_email_set = set()
                        for tu in _target_users_copy:
                            email = tu.get('email', '').strip()
                            if email and email != _actor_email and email not in all_notified:
                                create_notification(
                                    email,
                                    ntype='announcement',
                                    title=f'[For You] {_post_title}',
                                    message=f'Shared with you by {_actor_label}',
                                    link=_post_link,
                                )
                                target_email_set.add(email)
                                all_notified.add(email)
                        # 5) 대상 사용자 이메일 발송 (선택적)
                        if _notify_target_email and _target_users_copy:
                            try:
                                from app.services.otp_service import send_eval_reminder_email
                                all_target_emails = {tu['email'] for tu in _target_users_copy if tu.get('email')}
                                all_target_emails.discard(_actor_email)
                                for email in all_target_emails:
                                    try:
                                        send_eval_reminder_email(
                                            email,
                                            subject=f'[DYB NHR] {_post_title}',
                                            body_text=f'A new post has been shared with you.\n\nTitle: {_post_title}\nBy: {_actor_label}\n\nView it here: {_post_link}',
                                        )
                                    except Exception:
                                        logger.exception('target user email failed: %s', email)
                            except Exception:
                                logger.exception('target user email import/dispatch failed')
                    except Exception:
                        logger.exception('announcement notification dispatch failed')
                _notif_pool.submit(_send_notifs)
            except Exception:
                logger.debug('notification dispatch skipped', exc_info=True)

        return jsonify({
            'status': 'SUCCESS',
            'post_id': final_id,
            'version': new_version,
        })
    except Exception:
        logger.exception('api_save error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Delete API ────────────────────────────────────────────

@announcements_bp.route('/api/announcements/delete/<post_id>', methods=['POST'])
@api_admin_required
@limiter.limit("5 per minute", key_func=admin_rate_key)
def api_delete(post_id):
    try:
        db = get_firestore_client()
        ref = db.collection('announcements').document(post_id)
        snap = ref.get()
        if not snap.exists:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404

        # delete subcollection comments (best-effort, batched)
        try:
            comments = ref.collection('comments').stream()
            batch = db.batch()
            i = 0
            for c in comments:
                batch.delete(c.reference)
                i += 1
                if i % 400 == 0:
                    batch.commit()
                    batch = db.batch()
            if i % 400 != 0:
                batch.commit()
        except Exception:
            logger.exception('api_delete: subcollection wipe failed %s', post_id)

        # delete poll_votes subcollection (best-effort)
        try:
            poll_votes = ref.collection('poll_votes').stream()
            batch = db.batch()
            i = 0
            for pv in poll_votes:
                batch.delete(pv.reference)
                i += 1
                if i % 400 == 0:
                    batch.commit()
                    batch = db.batch()
            if i % 400 != 0:
                batch.commit()
        except Exception:
            logger.exception('api_delete: poll_votes wipe failed %s', post_id)

        # delete reactions subcollection (best-effort)
        try:
            reactions = ref.collection('reactions').stream()
            batch = db.batch()
            i = 0
            for rx in reactions:
                batch.delete(rx.reference)
                i += 1
                if i % 400 == 0:
                    batch.commit()
                    batch = db.batch()
            if i % 400 != 0:
                batch.commit()
        except Exception:
            logger.exception('api_delete: reactions wipe failed %s', post_id)

        ref.delete()

        # Storage prefix 삭제 (best-effort)
        try:
            delete_prefix(f'announcements/{post_id}/')
        except Exception:
            logger.exception('api_delete: storage wipe failed %s', post_id)

        log_audit('announcement_delete',
                  actor=session.get('admin_email', ''),
                  target=post_id,
                  category='announcement')

        try:
            from app.services.cache_service import invalidate_top_announcements
            invalidate_top_announcements()
        except Exception:
            pass

        return jsonify({'status': 'SUCCESS'})
    except Exception:
        logger.exception('api_delete error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Upload APIs ───────────────────────────────────────────

@announcements_bp.route('/api/announcements/upload-image', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_upload_image():
    try:
        if not is_storage_enabled():
            return jsonify({'status': 'ERROR', 'message': 'Storage not configured.'}), 503
        f = request.files.get('file')
        if not f:
            return jsonify({'status': 'ERROR', 'message': 'file field required.'}), 400
        post_id = (request.form.get('post_id') or '').strip() or None
        prefix = _upload_prefix(post_id, 'images')
        try:
            result = upload_image(f, prefix)
        except UploadRejected as e:
            return jsonify({'status': 'ERROR', 'message': str(e)}), 400
        except StorageDisabled:
            return jsonify({'status': 'ERROR', 'message': 'Storage disabled.'}), 503

        log_audit('announcement_image_upload',
                  actor=session.get('admin_email', ''),
                  target=post_id or '_tmp',
                  details={
                      'path': result['path'],
                      'original_size': result['original_size'],
                      'stored_size': result['size'],
                      'resized': result['resized'],
                  },
                  category='announcement')
        return jsonify({'status': 'SUCCESS', **result})
    except Exception:
        logger.exception('api_upload_image error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/upload-attachment', methods=['POST'])
@api_admin_required
@limiter.limit("20 per minute", key_func=admin_rate_key)
def api_upload_attachment():
    try:
        if not is_storage_enabled():
            return jsonify({'status': 'ERROR', 'message': 'Storage not configured.'}), 503
        f = request.files.get('file')
        if not f:
            return jsonify({'status': 'ERROR', 'message': 'file field required.'}), 400
        post_id = (request.form.get('post_id') or '').strip() or None
        prefix = _upload_prefix(post_id, 'files')
        try:
            result = upload_attachment(f, prefix)
        except UploadRejected as e:
            return jsonify({'status': 'ERROR', 'message': str(e)}), 400
        except StorageDisabled:
            return jsonify({'status': 'ERROR', 'message': 'Storage disabled.'}), 503

        log_audit('announcement_attachment_upload',
                  actor=session.get('admin_email', ''),
                  target=post_id or '_tmp',
                  details={'path': result['path'], 'size': result['size'], 'filename': result['filename']},
                  category='announcement')
        return jsonify({'status': 'SUCCESS', **result})
    except Exception:
        logger.exception('api_upload_attachment error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Comments ──────────────────────────────────────────────

@announcements_bp.route('/api/announcements/<post_id>/comments', methods=['GET'])
def api_comments_list(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        docs = (ref.collection('comments')
                .order_by('created_at', direction='ASCENDING')
                .limit(500).stream())
        me = session.get('admin_email', '')
        items = []
        for d in docs:
            dd = d.to_dict() or {}
            liked_by = dd.get('liked_by') or []
            items.append({
                'id': d.id,
                'content': dd.get('content', ''),
                'author_name': dd.get('author_name', ''),
                'author_email': dd.get('author_email', ''),
                'author_role': dd.get('author_role', ''),
                'author_campus': dd.get('author_campus', ''),
                'created_at': dd.get('created_at', ''),
                'edited_at': dd.get('edited_at'),
                'parent_id': dd.get('parent_id'),
                'deleted': bool(dd.get('deleted')),
                'like_count': int(dd.get('like_count', 0) or 0),
                'my_liked': me in liked_by,
            })
        return jsonify({'status': 'SUCCESS', 'items': items})
    except Exception:
        logger.exception('api_comments_list error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/comments', methods=['POST'])
@limiter.limit("10 per minute", key_func=_user_rate_key)
def api_comment_create(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        data = request.get_json(silent=True) or {}
        content = strip_to_text(data.get('content', ''), max_len=_MAX_COMMENT_LEN)
        if not content:
            return jsonify({'status': 'ERROR', 'message': 'Content is required.'}), 400

        # Optional reply target — only 1 level deep, parent must be a root comment
        parent_id = data.get('parent_id')
        if parent_id is not None:
            parent_id = str(parent_id).strip() or None
        if parent_id:
            parent_snap = ref.collection('comments').document(parent_id).get()
            if not parent_snap.exists:
                return jsonify({'status': 'ERROR', 'message': 'Parent comment not found.'}), 400
            parent_data = parent_snap.to_dict() or {}
            if parent_data.get('parent_id'):
                return jsonify({'status': 'ERROR', 'message': 'Cannot reply to a reply.'}), 400
            if parent_data.get('deleted'):
                return jsonify({'status': 'ERROR', 'message': 'Cannot reply to a deleted comment.'}), 400

        actor_email = session.get('admin_email', '')
        author_name = session.get('emp_name', '') or actor_email
        # Look up campus from portal_users (best-effort)
        author_campus = ''
        try:
            user_doc = get_user_by_email(actor_email)
            if user_doc:
                author_campus = user_doc.get('campus', '')
        except Exception:
            pass
        payload = {
            'content':       content,
            'author_email':  actor_email,
            'author_name':   author_name,
            'author_role':   _session_role(),
            'author_campus': author_campus,
            'created_at':    _now_iso(),
            'parent_id':     parent_id,
        }
        c_ref = ref.collection('comments').document()
        c_ref.set(payload)
        # Denormalize commenter name onto the post for search
        try:
            ref.update({'comment_authors': firestore.ArrayUnion([author_name])})
        except Exception:
            pass
        log_audit('announcement_comment_create',
                  actor=actor_email,
                  target=f'{post_id}/{c_ref.id}',
                  category='announcement')
        return jsonify({'status': 'SUCCESS', 'id': c_ref.id, **payload})
    except Exception:
        logger.exception('api_comment_create error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/comments/<cid>', methods=['PATCH'])
@limiter.limit("20 per minute", key_func=_user_rate_key)
def api_comment_edit(post_id, cid):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        c_ref = ref.collection('comments').document(cid)
        snap = c_ref.get()
        if not snap.exists:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        c = snap.to_dict() or {}
        me = session.get('admin_email', '')
        if c.get('deleted'):
            return jsonify({'status': 'ERROR', 'message': 'Cannot edit a deleted comment.'}), 400
        if c.get('author_email') != me:
            return jsonify({'status': 'ERROR', 'message': 'Forbidden.'}), 403
        data = request.get_json(silent=True) or {}
        content = strip_to_text(data.get('content', ''), max_len=_MAX_COMMENT_LEN)
        if not content:
            return jsonify({'status': 'ERROR', 'message': 'Content is required.'}), 400
        c_ref.update({'content': content, 'edited_at': _now_iso()})
        log_audit('announcement_comment_edit',
                  actor=me,
                  target=f'{post_id}/{cid}',
                  category='announcement')
        return jsonify({'status': 'SUCCESS', 'content': content})
    except Exception:
        logger.exception('api_comment_edit error id=%s cid=%s', post_id, cid)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/comments/<cid>/like', methods=['POST'])
@limiter.limit("30 per minute", key_func=_user_rate_key)
def api_comment_like(post_id, cid):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        c_ref = ref.collection('comments').document(cid)
        me = session.get('admin_email', '')
        db = get_firestore_client()

        liked = False
        new_count = 0

        @firestore.transactional
        def _do_like(tx):
            nonlocal liked, new_count
            snap = c_ref.get(transaction=tx)
            if not snap.exists:
                raise ValueError('not_found')
            liked_by = list(snap.to_dict().get('liked_by') or [])
            if me in liked_by:
                liked_by.remove(me)
                liked = False
            else:
                liked_by.append(me)
                liked = True
            new_count = len(liked_by)
            tx.update(c_ref, {'liked_by': liked_by, 'like_count': new_count})

        try:
            _do_like(db.transaction())
        except ValueError:
            return jsonify({'status': 'ERROR', 'message': 'Comment not found.'}), 404

        return jsonify({'status': 'SUCCESS', 'liked': liked, 'count': new_count})
    except Exception:
        logger.exception('api_comment_like error id=%s cid=%s', post_id, cid)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/comments/<cid>', methods=['DELETE'])
@limiter.limit("20 per minute", key_func=_user_rate_key)
def api_comment_delete(post_id, cid):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        c_ref = ref.collection('comments').document(cid)
        snap = c_ref.get()
        if not snap.exists:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        c = snap.to_dict() or {}
        me = session.get('admin_email', '')
        if c.get('author_email') != me and not _is_admin():
            return jsonify({'status': 'ERROR', 'message': 'Forbidden.'}), 403
        if c.get('deleted'):
            return jsonify({'status': 'ERROR', 'message': 'Already deleted.'}), 400

        # Root comment with replies → soft-delete (preserve thread structure).
        # Reply or childless root → hard delete.
        soft = False
        if not c.get('parent_id'):
            try:
                has_reply = next(
                    iter(ref.collection('comments')
                         .where(filter=FieldFilter('parent_id', '==', cid))
                         .limit(1).stream()),
                    None,
                )
                if has_reply is not None:
                    soft = True
            except Exception:
                logger.exception('api_comment_delete: reply probe failed %s/%s', post_id, cid)

        if soft:
            c_ref.update({
                'deleted':       True,
                'deleted_at':    _now_iso(),
                'content':       '',
                'author_name':   '',
                'author_email':  '',
                'author_role':   '',
                'author_campus': '',
            })
        else:
            c_ref.delete()

        # comment_authors 재집계: 삭제 후 남은 non-deleted 댓글 작성자로 갱신
        try:
            remaining = ref.collection('comments').stream()
            authors = list(dict.fromkeys(
                doc.to_dict().get('author_name', '')
                for doc in remaining
                if not doc.to_dict().get('deleted')
                and doc.to_dict().get('author_name')
            ))
            ref.update({'comment_authors': authors})
        except Exception:
            logger.exception('api_comment_delete: comment_authors recalc failed %s', post_id)

        log_audit('announcement_comment_delete',
                  actor=me,
                  target=f'{post_id}/{cid}',
                  category='announcement')
        return jsonify({'status': 'SUCCESS'})
    except Exception:
        logger.exception('api_comment_delete error id=%s cid=%s', post_id, cid)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Poll APIs ─────────────────────────────────────────────

@announcements_bp.route('/api/announcements/<post_id>/poll', methods=['GET'])
def api_poll_get(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, data = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        poll = data.get('poll') or {}
        if not poll.get('enabled'):
            return jsonify({'status': 'SUCCESS', 'poll': None})

        options = poll.get('options') or []
        mode = poll.get('mode', 'named')
        me = session.get('admin_email', '')
        my_key = _voter_key(me, mode)

        votes_snap = ref.collection('poll_votes').stream()
        votes = {d.id: d.to_dict() for d in votes_snap}

        my_vote = (votes.get(my_key) or {}).get('option_id')

        counts = {opt['id']: 0 for opt in options}
        named_voters = {opt['id']: [] for opt in options}
        total = 0
        for key, v in votes.items():
            oid = v.get('option_id')
            if oid in counts:
                counts[oid] += 1
                total += 1
                if mode == 'named':
                    named_voters[oid].append(v.get('voter_name', ''))

        result_options = []
        for opt in options:
            o = {
                'id': opt['id'],
                'label': opt['label'],
                'count': counts.get(opt['id'], 0),
            }
            if mode == 'named' and (my_vote or _is_admin()):
                o['voters'] = named_voters.get(opt['id'], [])
            result_options.append(o)

        return jsonify({
            'status': 'SUCCESS',
            'poll': {
                'question': poll.get('question', ''),
                'mode': mode,
                'options': result_options,
                'total': total,
                'my_vote': my_vote,
                'ended': _is_poll_ended(poll),
                'ends_at': poll.get('ends_at'),
            }
        })
    except Exception:
        logger.exception('api_poll_get error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/poll/vote', methods=['POST'])
@limiter.limit("10 per minute", key_func=_user_rate_key)
def api_poll_vote(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, data = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        poll = data.get('poll') or {}
        if not poll.get('enabled'):
            return jsonify({'status': 'ERROR', 'message': 'Poll not available.'}), 400
        if _is_poll_ended(poll):
            return jsonify({'status': 'ERROR', 'message': 'This poll has ended.'}), 400

        body = request.get_json(silent=True) or {}
        option_id = str(body.get('option_id', '')).strip()
        valid_ids = {opt['id'] for opt in (poll.get('options') or [])}
        if option_id not in valid_ids:
            return jsonify({'status': 'ERROR', 'message': 'Invalid option.'}), 400

        me = session.get('admin_email', '')
        mode = poll.get('mode', 'named')
        my_key = _voter_key(me, mode)

        vote_doc: dict = {'option_id': option_id, 'voted_at': _now_iso()}
        if mode == 'named':
            vote_doc['voter_name'] = session.get('emp_name', '') or me
            vote_doc['voter_email'] = me
        # anonymous: only hashed key stored, no email

        ref.collection('poll_votes').document(my_key).set(vote_doc)
        log_audit('announcement_poll_vote',
                  actor=me, target=f'{post_id}/{option_id}',
                  category='announcement')
        return jsonify({'status': 'SUCCESS'})
    except Exception:
        logger.exception('api_poll_vote error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/poll/vote', methods=['DELETE'])
@limiter.limit("10 per minute", key_func=_user_rate_key)
def api_poll_unvote(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, data = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        poll = data.get('poll') or {}
        if not poll.get('enabled'):
            return jsonify({'status': 'ERROR', 'message': 'Poll not available.'}), 400
        if _is_poll_ended(poll):
            return jsonify({'status': 'ERROR', 'message': 'This poll has ended.'}), 400

        me = session.get('admin_email', '')
        mode = poll.get('mode', 'named')
        my_key = _voter_key(me, mode)

        ref.collection('poll_votes').document(my_key).delete()
        log_audit('announcement_poll_unvote',
                  actor=me, target=post_id,
                  category='announcement')
        return jsonify({'status': 'SUCCESS'})
    except Exception:
        logger.exception('api_poll_unvote error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Last-read tracking ────────────────────────────────────

@announcements_bp.route('/api/announcements/last-read', methods=['GET'])
def api_last_read_get():
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        me = session.get('admin_email', '')
        if not me:
            return jsonify({'status': 'SUCCESS', 'last_read_at': None})
        snap = _last_read_doc(me).get()
        last = (snap.to_dict() or {}).get('last_read_at') if snap.exists else None
        return jsonify({'status': 'SUCCESS', 'last_read_at': last})
    except Exception:
        logger.exception('api_last_read_get error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/mark-read', methods=['POST'])
@limiter.limit("30 per minute", key_func=_user_rate_key)
def api_mark_read():
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        me = session.get('admin_email', '')
        if not me:
            return jsonify({'status': 'ERROR', 'message': 'No email on session.'}), 400
        now = _now_iso()
        _last_read_doc(me).set({'last_read_at': now}, merge=True)
        return jsonify({'status': 'SUCCESS', 'last_read_at': now})
    except Exception:
        logger.exception('api_mark_read error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Reactions ─────────────────────────────────────────────

@announcements_bp.route('/api/announcements/<post_id>/reactions', methods=['GET'])
def api_reactions_get(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        me = session.get('admin_email', '')
        my_key = _reaction_key(me)
        count = _reaction_count(ref)
        my_liked = ref.collection('reactions').document(my_key).get().exists
        return jsonify({'status': 'SUCCESS', 'count': count, 'my_liked': my_liked})
    except Exception:
        logger.exception('api_reactions_get error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@announcements_bp.route('/api/announcements/<post_id>/react', methods=['POST'])
@limiter.limit("30 per minute", key_func=_user_rate_key)
def api_react_toggle(post_id):
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        ref, _ = _load_post_for_user(post_id)
        if ref is None:
            return jsonify({'status': 'ERROR', 'message': 'Not found.'}), 404
        me = session.get('admin_email', '')
        my_key = _reaction_key(me)
        r_ref = ref.collection('reactions').document(my_key)
        snap = r_ref.get()
        if snap.exists:
            r_ref.delete()
            liked = False
        else:
            r_ref.set({'email': me, 'created_at': _now_iso()})
            liked = True
        count = _reaction_count(ref)
        return jsonify({'status': 'SUCCESS', 'liked': liked, 'count': count})
    except Exception:
        logger.exception('api_react_toggle error id=%s', post_id)
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})
