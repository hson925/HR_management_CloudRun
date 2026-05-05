SERVICE  = example-service
REGION   = asia-northeast1
LOG_FILTER = resource.type=cloud_run_revision AND resource.labels.service_name=$(SERVICE)

.PHONY: deploy logs logs-all url

## 배포
deploy:
	gcloud run deploy $(SERVICE) --source . --region $(REGION) --allow-unauthenticated

## 최근 에러 로그 20건
logs:
	gcloud logging read "$(LOG_FILTER) AND severity=ERROR" \
	  --limit=20 --format="value(timestamp,textPayload)" --region=$(REGION)

## 최근 전체 로그 50건 (에러 외 포함)
logs-all:
	gcloud logging read "$(LOG_FILTER)" \
	  --limit=50 --format="value(timestamp,severity,textPayload)" --region=$(REGION)

## 서비스 URL 확인
url:
	gcloud run services describe $(SERVICE) --region=$(REGION) \
	  --format="value(status.url)"
