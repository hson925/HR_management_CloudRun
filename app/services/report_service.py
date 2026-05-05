"""
app/services/report_service.py
평가 결과지 PDF 생성 및 Drive 업로드 서비스
"""
import io
import logging
import unicodedata
from collections import defaultdict
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.asset_service import get_static_data_uri
from app.services import role_service

logger = logging.getLogger(__name__)


def _normalize_rater_name(name: str) -> str:
    """rater_name 정규화 — NFC + lower + strip. 동명이인/표기차이 통합 키."""
    return unicodedata.normalize('NFC', (name or '').strip().lower())


def select_effective_responses(responses: list) -> list:
    """평균·카운트·순위 산출용 effective 응답 리스트.

    같은 (emp_id, rater_role, normalized rater_name) 그룹 안에서 submitted_at 내림차순으로
    가장 최근 1건만 남김. 같은 평가자 재제출 시 점수 왜곡 방지.

    emp_id 가 키에 포함돼 있어 단일-teacher 응답이든 다중-teacher 응답 (랭킹용 all_responses)
    이든 동일하게 안전.

    is_test=True 응답은 항상 제외.
    is_manual=True 응답은 doc_id 별 독립 키 → admin 이 의도적으로 같은 사람을 여러 번
    수동 입력해도 모두 채택 (manual 끼리는 dedup 안 함).

    raw 응답 리스트는 변경하지 않음 (admin status 모달은 전체를 보고 결정).
    """
    by_key = {}
    for r in responses:
        if r.get('is_test', False):
            continue
        emp_id = r.get('emp_id', '')
        role = r.get('rater_role', '')
        if r.get('is_manual', False):
            # manual 은 doc_id 별 독립 키 — 모두 채택
            doc_id_marker = r.get('doc_id') or r.get('docId') or id(r)
            key = (emp_id, role, f'__manual__{doc_id_marker}')
        else:
            key = (emp_id, role, _normalize_rater_name(r.get('rater_name', '')))
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = r
        else:
            # ISO 8601 문자열 비교 (kst_now() 형식 일관)
            if str(r.get('submitted_at', '')) > str(existing.get('submitted_at', '')):
                by_key[key] = r
    return list(by_key.values())


def _detect_original_lang(answer: str, answer_ko: str, answer_en: str) -> str:
    """
    원문 언어 감지: 'ko' / 'en'
    GPT는 원문 언어를 그대로 반환하므로 answer와 비교.
    일치하지 않으면 한글 문자 비율로 판별.
    """
    ans = answer.strip()
    if answer_ko and answer_ko.strip() == ans:
        return 'ko'
    if answer_en and answer_en.strip() == ans:
        return 'en'
    ko_chars = sum(1 for c in ans if '\uAC00' <= c <= '\uD7A3' or '\u1100' <= c <= '\u11FF')
    # \uD55C\uAE00 \uBE44\uC728 \uC784\uACC4 5% \u2014 KO \uB2F5\uBCC0\uC5D0 \uC601\uC5B4 \uB2E8\uC5B4 \uB2E4\uC218 \uC11E\uC778 \uCF00\uC774\uC2A4 ('AI \uB3C4\uAD6C \uD65C\uC6A9 \uB2A5\uB825\uC774 outstanding \uD569\uB2C8\uB2E4')
    # \uB3C4 KO \uB85C \uD310\uC815. 0.2 (20%) \uBA74 \uC601\uC5B4 \uBE44\uC911 \uD070 KO \uB2F5\uBCC0\uC774 'en' \uC73C\uB85C \uC624\uD310\uB418\uC5B4 KR/ENG \uD45C\uC2DC \uC21C\uC11C\uAC00 \uAC70\uAFB8\uB85C \uB428.
    return 'ko' if len(ans) > 0 and ko_chars / len(ans) > 0.05 else 'en'


def _get_initials(name: str) -> str:
    """이름에서 first name 첫 글자 추출 (Mark Edward Carr → M)"""
    parts = name.strip().split()
    return parts[0][0].upper() if parts else '?'


def _role_pill_class(role_name: str) -> str:
    """역할명에서 CSS pill 클래스 결정"""
    r = role_name.upper()
    if 'GS' in r:
        return 'gs'
    if 'KT' in r or 'CO-TEACHER' in r:
        return 'kt'
    if '분원장' in r or 'BRANCH' in r or 'BM' in r:
        return 'bm'
    if 'TL' in r or 'TEAM LEAD' in r:
        return 'tl'
    return ''


def _calc_avg(scores_list: list) -> float:
    """점수 리스트의 평균 계산"""
    vals = [float(v) for v in scores_list if v and float(v) > 0]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _calc_overall(role_avgs: dict, weights: dict) -> float:
    """가중치 반영 overall 점수 계산 — 응답 0개 역할은 가중치에서 제외 후 재정규화"""
    weighted_sum = 0.0
    weight_total = 0.0
    for role, avg in role_avgs.items():
        if avg == 0.0:
            continue  # 제출 없는 역할은 제외
        w = float(weights.get(role, 0))
        if w > 1:
            w = w / 100
        weighted_sum += avg * w
        weight_total += w
    if weight_total == 0:
        avgs = [v for v in role_avgs.values() if v > 0]
        return round(sum(avgs) / len(avgs), 2) if avgs else 0.0
    return round(weighted_sum / weight_total, 2)  # weight_total로 나눔으로써 자동 재정규화


def _calc_ranks(target_emp_id: str, eval_type: str, campus: str,
                all_responses: list, weights: dict, session_id: str) -> dict:
    """
    전체/캠퍼스 내 순위 계산
    all_responses: Firestore eval_v2_responses 전체 문서 리스트
    반환: {rank_all, total_all, rank_all_pct, rank_campus, total_campus, rank_campus_pct}
    """
    from app.services.roster_cache_service import get_roster

    # 로스터에서 사번 → 캠퍼스 맵 구성
    roster = get_roster()
    campus_map = {}
    for row in roster:
        if len(row) > 4:
            eid = str(row[2]).strip().lower()
            campus_map[eid] = str(row[4]).strip()

    # session_id 필터 + is_test 제외 + 같은 (emp/역할/이름) 그룹의 최신만 채택
    pre_filtered = [r for r in all_responses
                    if r.get('eval_type') == eval_type
                    and (not session_id or r.get('session_id') == session_id)]
    filtered = select_effective_responses(pre_filtered)

    # 사번별 역할별 점수 집계
    scores_by_emp = defaultdict(lambda: defaultdict(list))
    for r in filtered:
        eid = r.get('emp_id', '')
        role = r.get('rater_role', '')
        vals = [float(v) for v in r.get('scores', {}).values() if float(v) > 0]
        if vals:
            scores_by_emp[eid][role].append(sum(vals) / len(vals))

    # 사번별 overall 계산
    overall_map = {}
    for eid, role_scores in scores_by_emp.items():
        role_avgs = {role: sum(vs) / len(vs) for role, vs in role_scores.items()}
        overall_map[eid] = _calc_overall(role_avgs, weights)

    if target_emp_id not in overall_map:
        overall_map[target_emp_id] = 0.0

    # 전체 순위
    sorted_all = sorted(overall_map.items(), key=lambda x: x[1], reverse=True)
    rank_all = next((i + 1 for i, (eid, _) in enumerate(sorted_all) if eid == target_emp_id), len(sorted_all))
    total_all = len(sorted_all)

    # 캠퍼스 내 순위
    target_campus = campus_map.get(target_emp_id.lower(), campus)
    campus_overall = {eid: s for eid, s in overall_map.items()
                      if campus_map.get(eid, '') == target_campus}
    if target_emp_id not in campus_overall:
        campus_overall[target_emp_id] = overall_map[target_emp_id]
    sorted_campus = sorted(campus_overall.items(), key=lambda x: x[1], reverse=True)
    rank_campus = next((i + 1 for i, (eid, _) in enumerate(sorted_campus) if eid == target_emp_id), len(sorted_campus))
    total_campus = len(sorted_campus)

    def pct(r, t):
        return round(r / t * 100, 1) if t else 0.0

    return {
        'rank_all': rank_all,
        'total_all': total_all,
        'rank_all_pct': pct(rank_all, total_all),
        'rank_campus': rank_campus,
        'total_campus': total_campus,
        'rank_campus_pct': pct(rank_campus, total_campus),
    }

def calc_ranks_map(eval_type: str, all_responses: list,
                   weights: dict, session_id: str) -> dict:
    """
    전체 교사의 랭킹을 한 번에 계산하여 맵으로 반환
    반환: { emp_id: {rank_all, total_all, rank_all_pct, rank_campus, total_campus, rank_campus_pct} }
    """
    from app.services.roster_cache_service import get_roster

    roster = get_roster()
    campus_map = {}
    for row in roster:
        if len(row) > 4:
            eid = str(row[2]).strip().lower()
            campus_map[eid] = str(row[4]).strip()

    pre_filtered = [r for r in all_responses
                    if r.get('eval_type') == eval_type
                    and (not session_id or r.get('session_id') == session_id)]
    filtered = select_effective_responses(pre_filtered)

    scores_by_emp = defaultdict(lambda: defaultdict(list))
    for r in filtered:
        eid = r.get('emp_id', '')
        role = r.get('rater_role', '')
        vals = [float(v) for v in r.get('scores', {}).values() if float(v) > 0]
        if vals:
            scores_by_emp[eid][role].append(sum(vals) / len(vals))

    overall_map = {}
    for eid, role_scores in scores_by_emp.items():
        role_avgs = {role: sum(vs) / len(vs) for role, vs in role_scores.items()}
        overall_map[eid] = _calc_overall(role_avgs, weights)

    def pct(r, t):
        return round(r / t * 100, 1) if t else 0.0

    sorted_all = sorted(overall_map.items(), key=lambda x: x[1], reverse=True)
    total_all = len(sorted_all)
    rank_all_map = {eid: i + 1 for i, (eid, _) in enumerate(sorted_all)}

    # 캠퍼스별 그룹화
    campus_groups = defaultdict(dict)
    for eid, score in overall_map.items():
        c = campus_map.get(eid, '')
        campus_groups[c][eid] = score

    campus_rank_map = {}
    for c, group in campus_groups.items():
        sorted_c = sorted(group.items(), key=lambda x: x[1], reverse=True)
        total_c = len(sorted_c)
        for i, (eid, _) in enumerate(sorted_c):
            campus_rank_map[eid] = (i + 1, total_c)

    result = {}
    for eid in overall_map:
        r_all = rank_all_map.get(eid, total_all)
        r_c, t_c = campus_rank_map.get(eid, (total_all, total_all))
        result[eid] = {
            'rank_all': r_all,
            'total_all': total_all,
            'rank_all_pct': pct(r_all, total_all),
            'rank_campus': r_c,
            'total_campus': t_c,
            'rank_campus_pct': pct(r_c, t_c),
        }
    return result

def build_report_context(emp_id: str, eval_type: str, eval_type_label: str,
                         teacher_name: str, campus: str, start_date: str,
                         session_label: str, responses: list,
                         questions_map: dict, weights: dict,
                         all_responses: list, session_id: str,
                         nickname: str = '', open_questions_map: dict = None,
                         pill_class_map: dict = None,
                         precomputed_ranks: dict = None,
                         min_count_map: dict = None) -> dict:
    """
    Jinja2 템플릿에 전달할 컨텍스트 딕셔너리 생성
    """
    # 평균/카운트 산출용 effective 응답 — 같은 (역할, 정규화 이름) 그룹의 최신 1건만.
    # manual 은 doc_id 별 모두 유지. is_test 제외.
    eff_responses = select_effective_responses(responses)
    by_role = defaultdict(list)
    for r in eff_responses:
        by_role[r['rater_role']].append(r)

    # 역할별 평균 계산
    role_avgs = {}
    for role, evals in by_role.items():
        all_vals = []
        for ev in evals:
            vals = [float(v) for v in ev.get('scores', {}).values() if float(v) > 0]
            if vals:
                all_vals.append(sum(vals) / len(vals))
        role_avgs[role] = round(sum(all_vals) / len(all_vals), 2) if all_vals else 0.0

    # overall
    overall = _calc_overall(role_avgs, weights)

    # 가중 점수 (100점 환산)
    weighted_100 = round(overall / 5 * 100, 1)

    # 순위 (사전 계산된 맵이 있으면 lookup, 없으면 직접 계산)
    if precomputed_ranks is not None:
        ranks = precomputed_ranks.get(emp_id, {
            'rank_all': 0, 'total_all': 0, 'rank_all_pct': 0.0,
            'rank_campus': 0, 'total_campus': 0, 'rank_campus_pct': 0.0,
        })
    else:
        ranks = _calc_ranks(emp_id, eval_type, campus, all_responses, weights, session_id)

    # 수동 입력 여부
    manual_roles = [r for r, evals in by_role.items()
                    if any(ev.get('is_manual', False) for ev in evals)]

    # 미완료 역할 감지 (min_count 미달)
    incomplete_roles = []
    if min_count_map:
        for role, min_c in min_count_map.items():
            actual = len([ev for ev in by_role.get(role, []) if not ev.get('is_test', False)])
            if actual < min_c:
                incomplete_roles.append({'role': role, 'actual': actual, 'required': min_c})
    is_incomplete = bool(incomplete_roles)

    # role_blocks (Page 1 블록) — GS 항상 첫 번째, KT(복수) 마지막
    # questions_map의 키 순서 기반 정렬 (routes.py에서 roles_raw 순서 그대로 전달됨)
    role_order_list = list(questions_map.keys())
    def role_sort_key(r):
        try:
            return role_order_list.index(r)
        except ValueError:
            return len(role_order_list)

    role_blocks = []
    for role, evals in sorted(by_role.items(), key=lambda x: role_sort_key(x[0])):
        qlist = questions_map.get(role, [])
        raters = []
        for ev in evals:
            scores_ordered = []
            for i, q in enumerate(qlist):
                qid = q.get('id') or f'q{i+1}'
                val = ev.get('scores', {}).get(qid, 0)
                scores_ordered.append({'score': int(val) if val else 0})
            rater_avg_vals = [s['score'] for s in scores_ordered if s['score'] > 0]
            raters.append({
                'name': ev.get('rater_name', ''),
                'is_manual': ev.get('is_manual', False),
                'scores': scores_ordered,
                'avg': round(sum(rater_avg_vals) / len(rater_avg_vals), 2) if rater_avg_vals else 0.0,
            })

        w = float(weights.get(role, 0))
        if w > 1:
            w /= 100
        weighted_role = round(role_avgs.get(role, 0) * w, 2)

        role_blocks.append({
            'role': role,
            'label': role_service.get_role_label(role),
            'raters': raters,
            'avg': role_avgs.get(role, 0.0),
            'weighted': weighted_role,
            'full_width': len(evals) > 1 or len(questions_map.get(role, [])) > 6,
        })

    role_blocks.sort(key=lambda b: b['full_width'])

    # 단일 블록이 홀수 개면 마지막 단일 블록을 full_width로 확장
    half_blocks = [b for b in role_blocks if not b['full_width']]
    if len(half_blocks) % 2 == 1:
        half_blocks[-1]['full_width'] = True

    # role_details (Page 2 상세)
    role_details = []
    for role, evals in sorted(by_role.items(), key=lambda x: role_sort_key(x[0])):
        qlist = questions_map.get(role, [])
        # 문항별 평균 계산
        q_avgs = {}
        for ev in evals:
            for i, q in enumerate(qlist):
                qid = q.get('id') or f'q{i+1}'
                val = float(ev.get('scores', {}).get(qid, 0) or 0)
                if qid not in q_avgs:
                    q_avgs[qid] = []
                if val > 0:
                    q_avgs[qid].append(val)

        questions = []
        for i, q in enumerate(qlist):
            qid = q.get('id') or f'q{i+1}'
            vals = q_avgs.get(qid, [])
            avg_score = round(sum(vals) / len(vals), 1) if vals else 0.0
            descriptions = q.get('descriptions') or {}
            max_score = q.get('max_score') if isinstance(q.get('max_score'), int) else 5
            # 평균점수 가장 가까운 정수 키의 설명 추출
            level_desc = None
            if descriptions and avg_score > 0:
                nearest = max(1, min(max_score, round(avg_score)))
                d = descriptions.get(str(nearest))
                if d and (d.get('ko') or d.get('en')):
                    level_desc = {
                        'ko': d.get('ko', ''),
                        'en': d.get('en', ''),
                        'level': nearest,
                    }
            questions.append({
                'ko': q.get('text_ko') or q.get('ko', ''),
                'en': q.get('text_en') or q.get('en', ''),
                'avg_score': avg_score,
                'max_score': max_score,
                'level_description': level_desc,
            })

        # 평가자별 점수 (복수 평가자용 — KT 등)
        rater_details = []
        for ev in evals:
            r_scores = []
            for i, q in enumerate(qlist):
                qid = q.get('id') or f'q{i+1}'
                val = float(ev.get('scores', {}).get(qid, 0) or 0)
                r_scores.append(val)
            r_avg_vals = [s for s in r_scores if s > 0]
            rater_details.append({
                'name': ev.get('rater_name', ''),
                'is_manual': ev.get('is_manual', False),
                'scores': r_scores,
                'avg': round(sum(r_avg_vals) / len(r_avg_vals), 2) if r_avg_vals else 0.0,
            })

        # 역할별 코멘트 및 서술형 답변 수집
        role_ko_parts, role_en_parts = [], []
        for ev in evals:
            name = ev.get('rater_name', '')
            if ev.get('comment_ko'):
                role_ko_parts.append({'rater': name, 'text': ev['comment_ko']})
            if ev.get('comment_en'):
                role_en_parts.append({'rater': name, 'text': ev['comment_en']})
        oq_list = (open_questions_map or {}).get(role, [])
        oq_map = {oq.get('id', ''): oq for oq in oq_list}
        open_answers_by_rater = []
        for ev in evals:
            name = ev.get('rater_name', '')
            answers_ko = ev.get('open_answers_ko', {})
            answers_en = ev.get('open_answers_en', {})
            rater_oq_answers = []
            for oq_id, ans_val in ev.get('open_answers', {}).items():
                if ans_val and str(ans_val).strip():
                    oq = oq_map.get(oq_id, {})
                    q_text_ko = oq.get('text_ko') or oq.get('text_en') or oq_id
                    q_text_en = oq.get('text_en') or ''
                    ans_ko = str(answers_ko.get(oq_id, '')).strip()
                    ans_en = str(answers_en.get(oq_id, '')).strip()
                    orig = str(ans_val).strip()
                    orig_lang = _detect_original_lang(orig, ans_ko, ans_en)
                    # AI 번역이 안 돈 응답은 answers_ko/en 이 비어있으므로 원문을 해당 언어 슬롯에 backfill
                    if orig_lang == 'ko' and not ans_ko:
                        ans_ko = orig
                    elif orig_lang == 'en' and not ans_en:
                        ans_en = orig
                    rater_oq_answers.append({
                        'q_ko': q_text_ko,
                        'q_en': q_text_en,
                        'answer_ko': ans_ko,
                        'answer_en': ans_en,
                        'orig_lang': orig_lang,
                    })
            if rater_oq_answers:
                open_answers_by_rater.append({'rater': name, 'answers': rater_oq_answers})

        role_details.append({
            'role': role,
            'label': role_service.get_role_label(role),
            'pill_class': (pill_class_map or {}).get(role) if pill_class_map else _role_pill_class(role),
            'raters': [ev.get('rater_name', '') for ev in evals],
            'avg': role_avgs.get(role, 0.0),
            'questions': questions,
            'rater_details': rater_details,
            'comments_ko': role_ko_parts,
            'comments_en': role_en_parts,
            'open_answers_by_rater': open_answers_by_rater,
        })

    # comments
    comments = []
    for role, evals in by_role.items():
        ko_parts, en_parts = [], []
        for ev in evals:
            name = ev.get('rater_name', '')
            if ev.get('comment_ko'):
                ko_parts.append(f"[{name}] {ev['comment_ko']}")
            if ev.get('comment_en'):
                en_parts.append(f"[{name}] {ev['comment_en']}")
            oq_list = (open_questions_map or {}).get(ev.get('rater_role', ''), [])
            oq_map = {oq.get('id', ''): oq for oq in oq_list}
            for oq_id, ans_val in ev.get('open_answers', {}).items():
                if ans_val and str(ans_val).strip():
                    oq = oq_map.get(oq_id, {})
                    q_text = oq.get('text_ko') or oq.get('text_en') or oq_id
                    ko_parts.append(f"[{name}] {q_text}<br>{str(ans_val).strip()}")
        if ko_parts or en_parts:
            comments.append({
                'role': role,
                'pill_class': (pill_class_map or {}).get(role) if pill_class_map else _role_pill_class(role),
                'raters': [ev.get('rater_name', '') for ev in evals],
                'text_ko': '<br><br>'.join(ko_parts),
                'text_en': '<br><br>'.join(en_parts),
            })

    return {
        'teacher_name': teacher_name,
        'nickname': nickname,
        'emp_id': emp_id,
        'campus': campus,
        'start_date': start_date,
        'initials': _get_initials(teacher_name),
        'session_label': session_label,
        'eval_type_label': eval_type_label,
        'overall_score': overall,
        'weighted_score_100': weighted_100,
        'has_manual': bool(manual_roles),
        'manual_roles': manual_roles,
        'is_incomplete': is_incomplete,
        'incomplete_roles': incomplete_roles,
        'role_blocks': role_blocks,
        'role_details': role_details,
        'comments': comments,
        'logo_uri': get_static_data_uri('logo-white.png'),
        **ranks,
    }


def render_report_html(context: dict, template_dir: str) -> str:
    """Jinja2로 HTML 렌더링 — autoescape ON (평가자 답변 HTML 인젝션 차단)"""
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(['html', 'htm']),
    )
    template = env.get_template('report_template.html')
    return template.render(**context)


# WeasyPrint FontConfiguration 싱글톤 — 프로세스 내 다운로드 폰트 캐시 재유지.
# 첫 호출에서만 Google Fonts 등 원격 폰트를 다운로드하고, 이후 요청은 캐시를 재사용.
_weasy_font_config = None


def _get_font_config():
    global _weasy_font_config
    if _weasy_font_config is None:
        from weasyprint.text.fonts import FontConfiguration
        _weasy_font_config = FontConfiguration()
    return _weasy_font_config


def html_to_pdf(html_content: str) -> bytes:
    """
    WeasyPrint로 HTML → PDF 변환
    requirements.txt에 weasyprint 추가 필요
    """
    try:
        from weasyprint import HTML
        font_config = _get_font_config()
        pdf_bytes = HTML(string=html_content, base_url='/').write_pdf(font_config=font_config)
        return pdf_bytes
    except ImportError:
        raise ImportError(
            "weasyprint가 설치되지 않았습니다. "
            "requirements.txt에 'weasyprint' 추가 후 pip install 하세요."
        )