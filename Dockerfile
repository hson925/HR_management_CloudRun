# 가벼운 파이썬 3.10 버전을 기반으로 합니다
FROM python:3.10-slim

# 작업 폴더를 설정합니다
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      fonts-noto-cjk \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libpangocairo-1.0-0 \
    && fc-cache -f -v \
    && fc-list | grep -i noto \
    && rm -rf /var/lib/apt/lists/*

# 재료 목록을 먼저 복사하고 설치합니다 (캐싱 효율화)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 코드들을 다 복사합니다
COPY . .

# 서버를 실행합니다 (Gunicorn 사용)
CMD exec gunicorn --bind :$PORT --workers ${WORKERS:-1} --threads ${THREADS:-8} --timeout ${TIMEOUT:-120} --graceful-timeout 30 main:app