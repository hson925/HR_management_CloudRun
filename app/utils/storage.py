"""Firebase Storage 업로드 헬퍼 (이미지 + 일반 첨부).

이미지 파이프라인:
  MIME 검증 → Pillow 디코딩(매직바이트 확인) → EXIF 정규화 → 1920px 리사이즈
  → JPEG 재인코딩(q82) / GIF 원본 유지 / 투명 PNG 유지 → EXIF 전부 제거 → 업로드
  → 10년 signed URL 발급.

환경변수 `FIREBASE_STORAGE_BUCKET` 미설정 시 `is_storage_enabled()` False,
업로드 함수는 `StorageDisabled` 예외로 graceful degrade 한다.
"""
import io
import logging
import os
import uuid
from datetime import timedelta

from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


class StorageDisabled(Exception):
    """Firebase Storage 가 구성되지 않았을 때 발생."""


class UploadRejected(Exception):
    """MIME/크기/매직바이트 검증 실패."""


ALLOWED_IMAGE_MIME = {
    'image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif',
}
ALLOWED_ATTACHMENT_MIME = {
    # Video
    'video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/webm',
    'video/ogg', 'video/x-matroska', 'video/x-ms-wmv', 'video/mpeg',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/haansofthwp',
    'application/x-hwp',
    'application/vnd.hancom.hwp',
    'application/vnd.hancom.hwpx',
    'application/zip',
    'application/x-zip-compressed',
    'text/plain',
    'image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp',
    'image/svg+xml',
    'text/plain', 'text/html', 'text/css', 'text/javascript',
    'text/csv', 'text/x-python', 'text/x-shellscript',
    'application/json', 'application/javascript', 'application/typescript',
    'application/x-sh', 'application/xml',
}
ALLOWED_ATTACHMENT_EXTS = {
    # Documents
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'hwp', 'hwpx', 'zip', 'txt',
    # Images
    'jpg', 'jpeg', 'png', 'gif', 'webp',
    # Web
    'html', 'htm', 'css', 'scss', 'sass', 'less',
    'js', 'mjs', 'cjs', 'ts', 'jsx', 'tsx',
    'vue', 'svelte',
    # Data / Config
    'json', 'jsonc', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'env',
    'xml', 'csv', 'tsv',
    # Shell / Scripts
    'sh', 'bash', 'zsh', 'fish', 'bat', 'cmd', 'ps1',
    # Systems / General
    'c', 'h', 'cpp', 'hpp', 'cc', 'hh',
    'java', 'kt', 'kts',
    'swift', 'go', 'rs', 'rb', 'php',
    'cs', 'vb',
    'py', 'pyw', 'ipynb',
    'r', 'rmd',
    'sql',
    # Docs / Markup
    'md', 'rst', 'tex',
    # Video
    'mp4', 'mov', 'avi', 'webm', 'mkv', 'm4v', 'flv', 'wmv', 'mpeg', 'mpg',
    # Misc
    'dockerfile', 'makefile', 'svg',
    'lock', 'log',
}

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
IMAGE_MAX_DIMENSION = 1920
IMAGE_JPEG_QUALITY = 82

_SIGNED_URL_TTL = timedelta(days=3650)


def is_storage_enabled():
    return bool(os.environ.get('FIREBASE_STORAGE_BUCKET'))


def _get_bucket():
    if not is_storage_enabled():
        raise StorageDisabled('FIREBASE_STORAGE_BUCKET not set')
    from firebase_admin import storage
    return storage.bucket()


def _read_limited(file_storage, limit):
    """file.stream 에서 limit+1 바이트 읽어 크기 초과 여부를 검사."""
    stream = file_storage.stream
    try:
        stream.seek(0)
    except Exception:
        logger.debug('File stream not seekable; reading from current position')
    data = stream.read(limit + 1)
    if not data:
        raise UploadRejected('File is empty or could not be read')
    if len(data) > limit:
        raise UploadRejected(f'File too large (max {limit // (1024*1024)} MB)')
    return data


def upload_image(file_storage, path_prefix):
    """werkzeug FileStorage 를 받아 이미지 파이프라인 처리 후 업로드.

    path_prefix 예: `announcements/{post_id}/images`
    반환: {path, url, content_type, size, width, height, original_size, resized}
    """
    if not is_storage_enabled():
        raise StorageDisabled('Storage not configured')

    mime_in = (file_storage.mimetype or '').lower()
    if mime_in not in ALLOWED_IMAGE_MIME:
        raise UploadRejected(f'Unsupported image type: {mime_in}')

    raw = _read_limited(file_storage, MAX_IMAGE_UPLOAD_BYTES)
    original_size = len(raw)

    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise UploadRejected('Pillow not installed') from e

    # 무결성 검증 (verify 는 파일 포인터를 소비함)
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
    except Exception as e:
        raise UploadRejected(f'Invalid image data: {e}') from e

    img = Image.open(io.BytesIO(raw))
    is_animated_gif = (img.format == 'GIF' and getattr(img, 'is_animated', False))

    if is_animated_gif:
        # 애니메이션은 원본 그대로 업로드
        final_bytes = raw
        final_mime = 'image/gif'
        ext = 'gif'
        try:
            width, height = img.size
        except Exception:
            width = height = 0
        resized = False
    else:
        img = ImageOps.exif_transpose(img)
        original_mode = img.mode
        has_alpha = (original_mode in ('RGBA', 'LA', 'P')) and (
            'A' in original_mode or (
                original_mode == 'P' and 'transparency' in img.info
            )
        )

        resized = False
        if max(img.size) > IMAGE_MAX_DIMENSION:
            img.thumbnail(
                (IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION),
                Image.LANCZOS,
            )
            resized = True

        buf = io.BytesIO()
        if img.format == 'PNG' and has_alpha:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            img.save(buf, format='PNG', optimize=True)
            final_mime = 'image/png'
            ext = 'png'
        else:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(
                buf,
                format='JPEG',
                quality=IMAGE_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            final_mime = 'image/jpeg'
            ext = 'jpg'
        final_bytes = buf.getvalue()
        width, height = img.size

    filename = f'{uuid.uuid4().hex}.{ext}'
    blob_path = f'{path_prefix.rstrip("/")}/{filename}'

    bucket = _get_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(final_bytes, content_type=final_mime)
    try:
        url = blob.generate_signed_url(expiration=_SIGNED_URL_TTL, method='GET')
    except Exception:
        logger.exception('generate_signed_url failed for %s', blob_path)
        url = ''

    return {
        'path': blob_path,
        'url': url,
        'content_type': final_mime,
        'size': len(final_bytes),
        'width': int(width or 0),
        'height': int(height or 0),
        'original_size': original_size,
        'resized': resized,
    }


def upload_attachment(file_storage, path_prefix):
    """일반 파일 첨부 업로드. MIME + 확장자 화이트리스트.

    반환: {path, url, filename, content_type, size}
    """
    if not is_storage_enabled():
        raise StorageDisabled('Storage not configured')

    orig_name = (file_storage.filename or 'file').strip()
    safe_name = secure_filename(orig_name) or 'file'
    # secure_filename strips non-ASCII — ensure extension is still present
    ext = safe_name.rsplit('.', 1)[-1].lower() if '.' in safe_name else ''
    if not ext or ext not in ALLOWED_ATTACHMENT_EXTS:
        raise UploadRejected(f'Unsupported extension: .{ext or "(none)"}')

    mime_in = (file_storage.mimetype or '').lower()
    if mime_in and mime_in not in ALLOWED_ATTACHMENT_MIME:
        # 브라우저가 애매한 MIME 을 보낼 수 있어 확장자만 맞으면 허용
        logger.info('Attachment MIME %s not in allowlist; extension .%s ok', mime_in, ext)

    raw = _read_limited(file_storage, MAX_ATTACHMENT_BYTES)
    size = len(raw)
    if size == 0:
        raise UploadRejected('Empty file')

    stored_name = f'{uuid.uuid4().hex}__{safe_name}'
    blob_path = f'{path_prefix.rstrip("/")}/{stored_name}'

    bucket = _get_bucket()
    blob = bucket.blob(blob_path)
    escaped_name = safe_name.replace('\\', '\\\\').replace('"', '\\"')
    blob.content_disposition = f'attachment; filename="{escaped_name}"'
    blob.upload_from_string(raw, content_type=mime_in or 'application/octet-stream')
    try:
        url = blob.generate_signed_url(expiration=_SIGNED_URL_TTL, method='GET')
    except Exception:
        logger.exception('generate_signed_url failed for %s', blob_path)
        url = ''

    return {
        'path': blob_path,
        'url': url,
        'filename': safe_name,
        'content_type': mime_in or 'application/octet-stream',
        'size': size,
    }


def delete_prefix(prefix):
    """지정 prefix 하위의 모든 객체 삭제. 실패는 로그만."""
    if not is_storage_enabled():
        return 0
    try:
        bucket = _get_bucket()
        blobs = list(bucket.list_blobs(prefix=prefix))
        for b in blobs:
            try:
                b.delete()
            except Exception:
                logger.exception('delete_prefix: blob delete failed %s', b.name)
        return len(blobs)
    except Exception:
        logger.exception('delete_prefix failed prefix=%s', prefix)
        return 0
