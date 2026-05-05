import logging
import os
import json
import urllib.request

logger = logging.getLogger(__name__)

def translate_evaluation(text):
    if not text or len(str(text).strip()) < 2: return ""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: return "[Translation disabled: No API key] / [번역 기능 비활성화: API 키 없음]"
    url = "https://api.openai.com/v1/chat/completions"
    headers = { "Content-Type": "application/json", "Authorization": f"Bearer {api_key}" }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "당신은 DYB 교육의 전문 HR 번역가입니다. 원어민 선생님에 대한 영문 평가 코멘트를 전문적이고 자연스러운 한국어로 번역해 주세요."},
            {"role": "user", "content": text}
        ],
        "temperature": 0.3
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=30.0) as response:
            response_data = json.loads(response.read().decode('utf-8'))
            return response_data['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.exception('Translation Error: %s', e)
        return "[Translation system delay] / [번역 시스템 통신 지연]"

def translate_open_answers(open_answers: dict) -> dict:
    """
    서술형 답변들을 한국어·영어 양방향으로 번역합니다.
    언어를 자동 감지하여 두 언어 버전을 모두 반환합니다.

    반환값: {q_id: {'ko': '한국어 텍스트', 'en': 'English text'}}
    - 원문이 한국어 → ko: 원문 그대로, en: 영어 번역
    - 원문이 영어   → en: 원문 그대로, ko: 한국어 번역
    - 기타 언어     → 둘 다 번역
    """
    if not open_answers:
        return {}
    to_translate = {k: str(v).strip() for k, v in open_answers.items() if str(v).strip()}
    if not to_translate:
        return {}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {k: {'ko': v, 'en': v} for k, v in to_translate.items()}

    system = (
        "You are a professional HR translator for DYB Education. "
        "For each answer, detect the language and return BOTH a Korean and an English version. "
        "Rules: if the text is already Korean return it as-is in 'ko' and translate to English for 'en'. "
        "If the text is already English return it as-is in 'en' and translate to Korean for 'ko'. "
        "For any other language translate to both. "
        "Respond ONLY with a valid JSON object (no markdown, no explanation) in this exact format: "
        "{\"q_id\": {\"ko\": \"...\", \"en\": \"...\"}, ...}"
    )
    user_content = json.dumps(to_translate, ensure_ascii=False)

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.3,
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()

        parsed = json.loads(content)
        bilingual = {}
        for k, v in to_translate.items():
            entry = parsed.get(k, {})
            bilingual[k] = {
                'ko': str(entry.get('ko', v)).strip(),
                'en': str(entry.get('en', v)).strip(),
            }
        return bilingual
    except Exception as e:
        logger.exception('translate_open_answers error: %s', e)
        return {k: {'ko': v, 'en': v} for k, v in to_translate.items()}


def generate_narrative_summary(open_texts: list) -> str:
    """
    서술형 답변 목록을 강점 / 보완점 / 총평 3-섹션 한국어 종합 평가로 변환.
    open_texts: [{'question': str, 'answer': str}, ...]
    반환값: '## 강점\n...\n## 보완점\n...\n## 총평\n...' 형식 텍스트
    """
    if not open_texts:
        return ''
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return '[요약 기능 비활성화: API 키 없음]'

    system = (
        "당신은 DYB 교육 HR 담당자입니다. "
        "아래는 한 교사에 대한 여러 평가자의 서술형 답변 모음입니다. "
        "평가자들이 언급한 내용을 근거로 한국어 종합 평가 보고서를 작성하세요.\n\n"
        "출력 형식 (반드시 아래 구조를 그대로 따를 것):\n"
        "## 강점\n"
        "<평가자들이 언급한 긍정적 관찰·행동 사례를 2~4문장의 자연스러운 단락으로 서술>\n"
        "## 보완점\n"
        "<평가자들이 언급한 개선 사항·우려 사항을 2~4문장의 자연스러운 단락으로 서술. "
        "언급된 보완 사항이 없으면 '평가자들이 언급한 구체적인 보완 사항은 없습니다.' 로 작성>\n"
        "## 총평\n"
        "<평가자들의 전반적 평가와 인상을 2~4문장의 자연스러운 단락으로 서술>\n\n"
        "규칙:\n"
        "1. 평가자들이 언급하지 않은 내용은 절대 추가 금지.\n"
        "2. 언급된 내용을 근거로 한 판단·평가·해석은 허용. "
        "(예: '수업 준비가 철저하다'는 언급이 다수 → '수업 준비 측면에서 뚜렷한 강점이 있습니다.')\n"
        "3. 확장된 추측이나 언급 없는 영역까지 상상해 기술하지 말 것.\n"
        "4. 문어체 HR 보고서 톤. 불릿 기호(-, •) 사용 금지. 자연스러운 문단으로 작성.\n"
        "5. 각 섹션은 반드시 '## 강점', '## 보완점', '## 총평' 헤더로 시작.\n"
        "6. 번역된 답변은 의미 기준으로 중복 내용 한 번만 반영."
    )
    user_content = json.dumps(open_texts, ensure_ascii=False)

    url = 'https://api.openai.com/v1/chat/completions'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    payload = {
        'model': 'gpt-4o-mini',
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0.3,
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        return result['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.exception('generate_narrative_summary error: %s', e)
        return '[요약 생성 실패. 다시 시도해 주세요.]'


def generate_campus_summary(teacher_summaries: list) -> str:
    """
    캠퍼스 소속 각 교사의 개별 종합 평가를 입력으로, 캠퍼스 레벨 종합 평가 생성.
    teacher_summaries: [{'name': str, 'summary': str}, ...]
    반환값: '## 강점\\n...\\n## 보완점\\n...\\n## 총평\\n...' 형식
    """
    if not teacher_summaries:
        return ''
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return '[요약 기능 비활성화: API 키 없음]'

    system = (
        "당신은 DYB 교육 HR 담당자입니다. "
        "아래는 한 캠퍼스 소속 각 교사에 대해 이미 생성된 AI 종합 평가들의 모음입니다. "
        "이 평가들을 종합하여 해당 캠퍼스 전체의 인력 동향 보고서를 작성하세요.\n\n"
        "출력 형식 (반드시 아래 구조를 그대로 따를 것):\n"
        "## 강점\n"
        "<캠퍼스 전반에서 공통적으로 관찰되는 긍정적 특성을 3~5문장의 자연스러운 단락으로 서술>\n"
        "## 보완점\n"
        "<캠퍼스 전반에서 공통적으로 관찰되는 개선 필요 사항을 3~5문장의 자연스러운 단락으로 서술. "
        "없으면 '캠퍼스 수준에서 공통적으로 언급된 구체적인 보완 사항은 없습니다.' 로 작성>\n"
        "## 총평\n"
        "<캠퍼스 인력의 전반적 상태와 경향성에 대한 인상을 3~5문장의 자연스러운 단락으로 서술>\n\n"
        "규칙:\n"
        "1. 개별 교사 평가에 언급되지 않은 내용은 절대 추가 금지.\n"
        "2. 공통 패턴·반복되는 관찰 사항을 중심으로 종합하세요.\n"
        "3. 소수 교사에게만 해당되는 특이 사항은 '일부 교사에서 …' 로 표기.\n"
        "4. 개인 이름 나열 지양. 캠퍼스 전반의 경향성 중심으로 서술.\n"
        "5. 확장된 추측이나 언급 없는 영역까지 상상해 기술하지 말 것.\n"
        "6. 문어체 HR 보고서 톤. 불릿 기호(-, •) 사용 금지. 자연스러운 문단으로 작성.\n"
        "7. 각 섹션은 반드시 '## 강점', '## 보완점', '## 총평' 헤더로 시작."
    )
    user_content = json.dumps(teacher_summaries, ensure_ascii=False)

    url = 'https://api.openai.com/v1/chat/completions'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    payload = {
        'model': 'gpt-4o-mini',
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0.3,
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=90.0) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        return result['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.exception('generate_campus_summary error: %s', e)
        return '[캠퍼스 요약 생성 실패. 다시 시도해 주세요.]'

# ============================================================================
# 💡 Legal document article translation (KO → EN draft)
# ============================================================================

class LegalTranslateError(Exception):
    """번역 실패 — 호출자가 사용자에게 구체 에러 메시지 표시용."""
    def __init__(self, message, http_status=500):
        super().__init__(message)
        self.http_status = http_status


def translate_legal_article(title_ko: str, content_ko_html: str, model: str = 'gpt-4o') -> dict:
    """법적 문서 조항 1개를 한국어 → 영어로 번역.
    제목과 본문을 한 번의 API 호출로 처리 (JSON mode). HTML 구조 보존.
    반환: {'title_en': str, 'content_en_html': str, 'model': str,
           'input_tokens': int, 'output_tokens': int}
    실패 시 LegalTranslateError 예외.

    주의: 이 번역은 '초안' 이며 admin 검수 전이다. 법적 효력이 있는 번역으로
    간주하지 말 것 — 사용자에게 투명하게 draft 임을 표시해야 함.
    """
    title_ko = (title_ko or '').strip()
    content_ko_html = (content_ko_html or '').strip()
    if not title_ko and not content_ko_html:
        raise LegalTranslateError('Empty input — nothing to translate.', http_status=400)

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise LegalTranslateError('OPENAI_API_KEY not configured on server.', http_status=503)

    # 시스템 프롬프트 — 법률 번역가 역할 + HTML 보존 + JSON 출력 강제.
    # 용어집 최소 세트 (향후 확장 가능) — 일관성 핵심 용어.
    system = (
        'You are a professional legal translator for a Korean education company (DYB Education). '
        'Translate the given Korean legal document article into formal English suitable for a '
        'privacy policy or terms of service document. '
        'Strict rules:\n'
        '1. Preserve ALL HTML tags and attributes exactly as-is (<p>, <ul>, <li>, <strong>, '
        '<em>, <u>, <blockquote>, <h1>, <h2>, <h3>, <br>, spans with style, etc.). '
        'Only translate the text nodes.\n'
        '2. Use formal, precise legal English. Avoid colloquial expressions.\n'
        '3. Keep company/product names and emails unchanged.\n'
        '4. Use consistent terminology:\n'
        '   - 개인정보 → "personal information"\n'
        '   - 이용약관 → "Terms of Service"\n'
        '   - 개인정보 처리방침 → "Privacy Policy"\n'
        '   - 회원 / 이용자 → "user"\n'
        '   - 회사 → "the Company"\n'
        '   - 제N조 (xxx) → "Article N (xxx)"\n'
        '   - 제N장 → "Chapter N"\n'
        '5. Return ONLY a JSON object with keys "title_en" and "content_en_html". '
        'title_en is plain text (no HTML). content_en_html preserves all original HTML.\n'
        '6. If either input field is empty, return an empty string for that key.'
    )

    user_payload = {
        'title_ko': title_ko,
        'content_ko_html': content_ko_html,
    }

    url = 'https://api.openai.com/v1/chat/completions'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(user_payload, ensure_ascii=False)},
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=90.0) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # OpenAI 에러 상세 추출
        try:
            err_body = e.read().decode('utf-8', errors='replace')
        except Exception:
            err_body = ''
        logger.error('translate_legal_article OpenAI HTTP %s: %s', e.code, err_body[:400])
        if e.code == 429:
            raise LegalTranslateError('OpenAI rate limit — try again in a moment.', http_status=429)
        if e.code == 401:
            raise LegalTranslateError('OpenAI authentication failed — check API key.', http_status=503)
        raise LegalTranslateError(f'OpenAI API error (HTTP {e.code}).', http_status=502)
    except urllib.error.URLError as e:
        logger.exception('translate_legal_article network error')
        raise LegalTranslateError('Network error reaching OpenAI.', http_status=502)
    except Exception:
        logger.exception('translate_legal_article unexpected error')
        raise LegalTranslateError('Unexpected translation failure.', http_status=500)

    try:
        msg = (result.get('choices') or [{}])[0].get('message', {}).get('content', '')
        parsed = json.loads(msg)
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        logger.error('translate_legal_article: malformed response %r', result)
        raise LegalTranslateError('OpenAI returned malformed response.', http_status=502)

    usage = result.get('usage', {}) or {}
    return {
        'title_en':        str(parsed.get('title_en') or '').strip(),
        'content_en_html': str(parsed.get('content_en_html') or '').strip(),
        'model':           result.get('model', model),
        'input_tokens':    int(usage.get('prompt_tokens', 0) or 0),
        'output_tokens':   int(usage.get('completion_tokens', 0) or 0),
    }


# ============================================================================
# 💡 Eval v2 score-level descriptions translation (KO → EN)
# ============================================================================

class EvalTranslateError(Exception):
    """평가 번역 실패 — 호출자가 사용자에게 구체 에러 메시지 표시용."""
    def __init__(self, message, http_status=500):
        super().__init__(message)
        self.http_status = http_status


def translate_score_descriptions(question_context: str, max_score: int,
                                 descriptions_ko: dict, model: str = 'gpt-4o-mini') -> dict:
    """평가 문항의 점수별 설명을 한국어 → 영어 일괄 번역.
    descriptions_ko: {"1": "거의 매일 지각", "3": "월 1-2회", ...} — 빈 KO 키는 호출자가 미리 제거.
    반환: {'descriptions_en': {<동일 키>: <영문>}, 'model': str, 'input_tokens': int, 'output_tokens': int}
    실패 시 EvalTranslateError 예외.
    """
    if not isinstance(descriptions_ko, dict) or not descriptions_ko:
        raise EvalTranslateError('No Korean descriptions to translate.', http_status=400)

    # 빈 값 방어 (호출자에서 제거되어야 정상)
    cleaned = {str(k): str(v).strip() for k, v in descriptions_ko.items() if str(v).strip()}
    if not cleaned:
        raise EvalTranslateError('No non-empty Korean descriptions to translate.', http_status=400)

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise EvalTranslateError('OPENAI_API_KEY not configured on server.', http_status=503)

    safe_context = str(question_context or '').strip()[:500]
    try:
        safe_max = int(max_score)
    except (ValueError, TypeError):
        safe_max = 5

    system = (
        'You are a translator for DYB Education HR evaluation forms.\n'
        f'Context: each entry is the description of a score level on a {safe_max}-point scale '
        f'for the question: "{safe_context}"\n'
        'Translate each Korean score-level description into concise neutral English.\n'
        'Rules:\n'
        '1. Keep tone consistent across levels.\n'
        '2. Mirror numeric/frequency expressions (e.g. "주 2-3회" → "2-3 times per week").\n'
        '3. Preserve company/product/proper names unchanged.\n'
        '4. Do NOT add guidance or context that is not in the source.\n'
        '5. Aim for ~15 words or fewer; no trailing period unless natural.\n'
        '6. Return ONLY a JSON object: {"descriptions_en": {"<n>": "<english>", ...}} '
        'with the SAME keys as the input (do not add or drop keys).\n'
        '7. If a source value is empty (should not happen), return an empty string for that key.'
    )

    url = 'https://api.openai.com/v1/chat/completions'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps({'descriptions_ko': cleaned}, ensure_ascii=False)},
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8', errors='replace')
        except Exception:
            err_body = ''
        logger.error('translate_score_descriptions OpenAI HTTP %s: %s', e.code, err_body[:400])
        if e.code == 429:
            raise EvalTranslateError('OpenAI rate limit — try again in a moment.', http_status=429)
        if e.code == 401:
            raise EvalTranslateError('OpenAI authentication failed — check API key.', http_status=503)
        raise EvalTranslateError(f'OpenAI API error (HTTP {e.code}).', http_status=502)
    except urllib.error.URLError:
        logger.exception('translate_score_descriptions network error')
        raise EvalTranslateError('Network error reaching OpenAI.', http_status=502)
    except Exception:
        logger.exception('translate_score_descriptions unexpected error')
        raise EvalTranslateError('Unexpected translation failure.', http_status=500)

    try:
        msg = (result.get('choices') or [{}])[0].get('message', {}).get('content', '')
        parsed = json.loads(msg)
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        logger.error('translate_score_descriptions: malformed response %r', result)
        raise EvalTranslateError('OpenAI returned malformed response.', http_status=502)

    raw_en = parsed.get('descriptions_en') or {}
    if not isinstance(raw_en, dict):
        raise EvalTranslateError('OpenAI returned malformed descriptions_en.', http_status=502)

    # 입력 키만 응답에 포함 보장 (모델이 임의 키 추가하면 drop)
    descriptions_en = {}
    for k in cleaned.keys():
        v = raw_en.get(k)
        if v is None:
            descriptions_en[k] = ''
        else:
            descriptions_en[k] = str(v).strip()

    usage = result.get('usage', {}) or {}
    return {
        'descriptions_en': descriptions_en,
        'model':           result.get('model', model),
        'input_tokens':    int(usage.get('prompt_tokens', 0) or 0),
        'output_tokens':   int(usage.get('completion_tokens', 0) or 0),
    }
