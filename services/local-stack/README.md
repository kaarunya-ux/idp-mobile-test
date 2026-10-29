# Local integration stack: the three IDP Python services

conversion-service, extraction-service and ai-gateway on one machine, for integration testing with the Java
backend. Not for production: the passwords and secrets here are local, made-up values.

## Prepare once, ahead of time

```bash
cd services/local-stack
make prepare      # .env, the OCR models (65 MB), the infrastructure images, and our images (first time: minutes)
```

After `make prepare`, nothing is downloaded or built at start-up. Run it again after pulling new code.

## Start, check, stop

```bash
make up           # start everything and wait until it is healthy (about a minute)
make check        # the readiness check: health, tokens, the API, one extraction and one conversion end to end
make status       # what runs, and on which ports
make logs s=extraction-worker      # follow one service; `make logs` follows all
make down         # stop, keep the data
make reset        # stop and delete the data: databases, buckets, queues
```

`make check` ends with `N of N checks passed` when the stack is ready for the backend team.

## What runs

| Service | Port on this machine | What it is |
|---|---|---|
| `extraction-api` | 8000 | templates, designer, mobile bundle; `/health/ready`, `/metrics`, `/docs` |
| `extraction-intake`, `-relay`, `-worker`, `-worker-ai` | — | messages in, events out, the pipeline, deep analysis |
| `conversion-intake`, `-relay`, `-worker` | — | messages in, events out, the conversions |
| `conversion-health` | 8001 | `/health/ready` |
| `ai-gateway` | 8002 (this machine only) | internal: only extraction calls it |
| `mock-model` | — | stands in for the AI model server; made-up answers |
| `postgres`, `redis` | 15432 (this machine only) | our databases (`extraction_db`, `conversion_db`); the gateway's queue and limits |
| `rabbitmq` (mode B) | 5672, UI 15672 (guest/guest) | the message broker |
| `minio` (mode B) | 9000, console 9001 (local-access-key / local-secret-key) | buckets `idp-originals`, `idp-derived`, `idp-outputs`, `idp-templates` |
| `keycloak` (mode B) | 8080, admin console (admin / admin) | realm `idp`: test users and the service clients |
| `rabbit-setup` (mode B) | — | one-time job: the exchanges, and the queue `local.events-tap` that keeps the last 1,000 events |

## Two modes

**Mode B (the default): everything here.** The Java services point at this machine:

| They need | Value |
|---|---|
| RabbitMQ | `amqp://guest:guest@<this machine>:5672/`, exchanges `idp.commands` and `idp.events` |
| MinIO | `http://<this machine>:9000`, `local-access-key` / `local-secret-key` |
| Keycloak (issuer) | `http://<this machine>:8080/realms/idp` |
| extraction API (their gateway's upstream) | `http://<this machine>:8000` |

Set `PUBLIC_HOST` in `.env` to this machine's address (`hostname -I`) before they connect: it is the issuer in
every token, and it must be the address they use.

**Mode A: the backend's RabbitMQ, MinIO and Keycloak.** In `.env`, clear `COMPOSE_PROFILES` and fill in the
mode A block (addresses, keys, the `extraction-service` client secret). Our PostgreSQL, Redis and services still run
here. The backend team then needs:
- the four buckets;
- a queue bound to every event we publish;
- the Keycloak clients `extraction-service` and `conversion-service`, with audience `ai-gateway`.

Integration readiness doc, section 4.

## Test users (mode B)

Realm `idp`, client `idp-test` (password grant), password `local-password` for all three:

| User | Role | Can |
|---|---|---|
| `org-admin` | `ORG_ADMIN` | read, write and publish the test account's templates |
| `member` | `MEMBER` | read templates |
| `staff-admin` | `PLATFORM_ADMIN` | manage the standard templates |

Every token carries the test account `account_id = 0192f0aa-0000-7000-8000-00000000a001`. A token:

```bash
curl -s -d grant_type=password -d client_id=idp-test -d username=org-admin -d password=local-password \
  http://localhost:8080/realms/idp/protocol/openid-connect/token | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])"
```

## Sending work by hand

- **Conversion jobs:** `conversion-service/scripts/send_job.py`, the "fake Java" (`uv run python scripts/send_job.py
  report.docx --to PDF`); its defaults match this stack.
- **Extraction jobs:** `stack_check.py` shows the message shape.

Use made-up documents only, never real cards or ID numbers.

## Other machines reaching this one

If Ubuntu's firewall is on (`sudo ufw status`), open the ports the backend uses:

```bash
sudo ufw allow 5672,15672,9000,9001,8080,8000,8001/tcp
```

## When the real model server is ready

Set `MODEL_BASE_URL=http://10.0.3.66:4000/v1` in `.env` and `make up` again; nothing else changes.
