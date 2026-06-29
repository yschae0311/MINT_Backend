# Elasticsearch CA 인증서

관리형 Elasticsearch(HTTPS) 연결 시 동료에게 받은 `ca_certs.zip`을 이 폴더에 풀어 둡니다.

```bash
cd MINT_Backend
python3 scripts/install_es_ca.py ~/Downloads/ca_certs.zip
```

`.env` 예시:

```env
ELASTICSEARCH_URL=https://your-cluster:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=...
ELASTICSEARCH_CA_CERTS=certs/elasticsearch/http_ca.crt
```

애플리케이션 연결 방식:

```python
AsyncElasticsearch(
    ES_ADDR,
    ca_certs=CA_CRT,
    basic_auth=(ES_ID, ES_PW),
)
```

이 디렉터리의 `*.crt`, `*.pem` 파일은 git에 올리지 않습니다.
