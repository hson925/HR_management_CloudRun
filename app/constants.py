"""프로젝트 전역 상수.

사용 방법:
    from app.constants import ADMIN_ROLES, COL_PORTAL_USERS, ...
"""

# ── Admin / Role ──────────────────────────────────────────
# 'MASTER' 는 2026-04 마이그레이션 전 legacy 값. 마이그레이션 완료 검증 후 제거 예정.
ADMIN_ROLES: frozenset[str] = frozenset({'admin', 'MASTER'})

# '퇴사' 는 legacy 값. 신규 기록은 'retired' 사용. 기존 Firestore 데이터 호환을 위해 병행 허용.
RETIRED_ROLES: frozenset[str] = frozenset({'retired', '퇴사'})

ALL_PORTAL_ROLES: frozenset[str] = frozenset({
    'admin', 'MASTER',   # admin (MASTER = legacy)
    'NET', 'GS', 'TL', 'STL',  # staff roles
    'retired', '퇴사',   # retired (퇴사 = legacy)
})

# ── Firestore Collection Names ────────────────────────────
COL_PORTAL_USERS      = 'portal_users'
COL_AUDIT_LOGS        = 'audit_logs'

COL_EVAL_V2_SESSIONS         = 'eval_v2_sessions'
COL_EVAL_V2_RESPONSES        = 'eval_v2_responses'
COL_EVAL_V2_DRAFTS           = 'eval_v2_drafts'
COL_EVAL_V2_CONFIG           = 'eval_v2_config'
COL_EVAL_V2_SUMMARIES        = 'eval_v2_summaries'
COL_EVAL_V2_CAMPUS_SUMMARIES = 'eval_v2_campus_summaries'
COL_EVAL_V2_REPORTS          = 'eval_v2_reports'  # PDF 보고서 file_id 인덱스 — fast trash path

COL_NHR_ANNUAL_EVAL        = 'nhr_annual_eval'
COL_NHR_ANNUAL_EVAL_CONFIG = 'nhr_annual_eval_config'

# Days past deadline that an unfinished previous cycle can still be edited
# before the system silently rolls over to the next sequence. (갈래 B: 유예 기간)
ANNUAL_EVAL_GRACE_DAYS = 14

COL_ANNOUNCEMENTS     = 'announcements'
COL_NT_SESSIONS       = 'nt_sessions'
COL_NT_STUDENTS       = 'nt_students'

# ── Campus Code ↔ Korean Name Mappings ───────────────────
# 정렬 순서가 보존된 캠퍼스 목록 (드롭다운/테이블 등에서 사용)
CAMPUS_ORDER: list[str] = [
    'Campus A', 'Campus B', 'Campus C', 'Campus D', 'Campus E', 'Campus F',
    'Campus G', 'Campus H', 'Campus I', 'Campus J', 'Campus K', 'Campus L', 'Campus M', 'SUB',
]

# Korean name → English code (upper-case)
CAMPUS_KO_TO_CODE: dict[str, str] = {
    'Campus A': 'CMA', 'Campus B': 'CMB', 'Campus C': 'CMC', 'Campus D': 'CMD',
    'Campus E': 'CME', 'Campus F': 'CMF', 'Campus G': 'CMG', 'Campus H': 'CMH',
    'Campus I': 'CMI', 'Campus J': 'CMJ', 'Campus K': 'CMK', 'Campus L': 'CML',
    'Campus M': 'CMM', 'SUB': 'SUB',
}

# English code → Korean name (config.py CAMPUS_AUTH_CODES 와 동일)
CAMPUS_CODE_TO_KO: dict[str, str] = {v: k for k, v in CAMPUS_KO_TO_CODE.items()}
