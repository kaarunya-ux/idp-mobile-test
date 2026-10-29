"""The local stack's readiness check (``make check``): is everything up, and does work go through end to end?

1. Every API answers ``/health/ready``.
2. Keycloak issues a user token, and extraction's templates API and mobile bundle accept it.
3. One extraction: a generated card-like image (made-up text only) is uploaded to ``idp-originals``,
   ``ExtractionRequested`` goes to ``idp.commands``, and ``ExtractionCompleted`` (or ``WorkFailed``) comes back on
   ``idp.events``, with ``result.json`` in ``idp-derived``.
4. One conversion: a generated image becomes a PDF (``ConversionRequested`` -> ``ConversionCompleted``).

It plays the Java side the way ``conversion-service/scripts/send_job.py`` does. Exit code 0 only if every step passed.
"""

import asyncio
import io
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import aio_pika
import boto3
import httpx
from PIL import Image, ImageDraw, ImageFont

ACCOUNT = UUID("0192f0aa-0000-7000-8000-00000000a001")  # the test account in keycloak/idp-realm.json
HEALTH = {
    "extraction-api": "http://extraction-api:8000/health/ready",
    "ai-gateway": "http://ai-gateway:8002/health/ready",
    "conversion-health": "http://conversion-health:8001/health/ready",
}
TIMEOUT_S = 180
results: list[tuple[str, bool, str]] = []


def step(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)  # noqa: T201


def check_health() -> None:
    for name, url in HEALTH.items():
        try:
            response = httpx.get(url, timeout=5)
            step(f"{name} ready", response.status_code == httpx.codes.OK, f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            step(f"{name} ready", False, type(exc).__name__)


def check_api() -> None:
    try:
        token = httpx.post(
            os.environ["CHECK_TOKEN_URL"],
            data={
                "grant_type": "password",
                "client_id": "idp-test",
                "username": "org-admin",
                "password": "local-password",  # the local realm's made-up test user
            },
            timeout=10,
        ).json()["access_token"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        step("keycloak user token", False, type(exc).__name__)
        return
    step("keycloak user token", True)
    headers = {"Authorization": f"Bearer {token}"}
    templates = httpx.get("http://extraction-api:8000/api/v1/templates", headers=headers, timeout=10)
    count = len(templates.json().get("items", [])) if templates.status_code == httpx.codes.OK else 0
    step("templates API with the token", count >= 3, f"HTTP {templates.status_code}, {count} templates")  # noqa: PLR2004
    bundle = httpx.get("http://extraction-api:8000/api/v1/templates/bundle", headers=headers, timeout=10)
    again = httpx.get(
        "http://extraction-api:8000/api/v1/templates/bundle",
        headers=headers | {"If-None-Match": bundle.headers.get("ETag", "")},
        timeout=10,
    )
    codes = (bundle.status_code, again.status_code)
    step("mobile bundle with ETag", codes == (200, 304), f"{codes[0]}, then {codes[1]}")


def card_image() -> bytes:
    """A card with printed text, all made up (never a real card). The grey background keeps the mean brightness
    (about 0.75) inside the quality gate's range (QUALITY__BRIGHTNESS_RANGE, 0.25-0.90): a near-white card is
    refused as too bright (EXT-422-QUALITY-003), as a washed-out photo would be."""
    image = Image.new("RGB", (1600, 1000), (196, 200, 204))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
    except OSError:
        font = ImageFont.load_default(size=54)
    lines = ["INCOME TAX DEPARTMENT", "Permanent Account Number Card", "ABCPE1234F", "Name", "TEST PERSON",
             "Father's Name", "TEST PARENT", "Date of Birth", "01/01/1990"]
    for index, line in enumerate(lines):
        draw.text((90, 60 + index * 100), line, fill=(20, 20, 20), font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def envelope(message_type: str, item: UUID, payload: dict[str, Any]) -> tuple[bytes, str]:
    event_id = str(uuid4())
    body = {
        "messageId": event_id,
        "type": message_type,
        "version": 1,
        "occurredAt": datetime.now(UTC).isoformat(),
        "producer": "document-service",
        "correlationId": f"stack-check-{item}",
        "tenantId": str(ACCOUNT),
        "subjectId": str(item),
        "payload": payload,
    }
    return json.dumps(body).encode(), event_id


async def send_and_wait(routing_key: str, body: bytes, event_id: str, item: UUID, final: set[str]) -> dict[str, Any]:
    connection = await aio_pika.connect_robust(os.environ["RABBIT__URL"])
    async with connection:
        channel = await connection.channel()
        events = await channel.declare_exchange("idp.events", aio_pika.ExchangeType.TOPIC, durable=True)
        queue = await channel.declare_queue("", exclusive=True)
        await queue.bind(events, "#")
        commands = await channel.declare_exchange("idp.commands", aio_pika.ExchangeType.TOPIC, durable=True)
        await commands.publish(
            aio_pika.Message(body, headers={"eventId": event_id}, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=routing_key,
        )
        async with asyncio.timeout(TIMEOUT_S), queue.iterator() as messages:
            async for message in messages:
                async with message.process():
                    event = json.loads(message.body)
                    if event.get("subjectId") == str(item) and event.get("type") in final:
                        return event
    return {}


def s3() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.environ["STORAGE__ENDPOINT"],
        aws_access_key_id=os.environ["STORAGE__ACCESS_KEY"],
        aws_secret_access_key=os.environ["STORAGE__SECRET_KEY"],
        region_name="us-east-1",
    )


def check_extraction() -> None:
    document, item = uuid4(), uuid4()
    key = f"{ACCOUNT}/{document}/file-1"
    s3().put_object(Bucket="idp-originals", Key=key, Body=card_image(), ContentType="image/png")
    payload = {
        "jobId": str(uuid4()),
        "itemId": str(item),
        "documentId": str(document),
        "files": [{"bucket": "idp-originals", "key": key, "contentType": "image/png"}],
        "templateId": "auto",
    }
    body, event_id = envelope("ExtractionRequested", item, payload)
    started = time.monotonic()
    try:
        event = asyncio.run(
            send_and_wait("extraction.requested.standard", body, event_id, item, {"ExtractionCompleted", "WorkFailed"})
        )
    except TimeoutError:
        step("extraction end to end", False, f"no answer in {TIMEOUT_S} s: is extraction-worker running?")
        return
    seconds = f"{time.monotonic() - started:.1f} s"
    if event.get("type") == "WorkFailed":
        step("extraction end to end", False, f"WorkFailed {event['payload']['error']['code']} after {seconds}")
        return
    result = event.get("payload", {})
    stored = json.loads(s3().get_object(Bucket="idp-derived", Key=result["resultKey"])["Body"].read())
    summary = result.get("summary", {})
    detail = (
        f"{result.get('documentType')}, {summary.get('fields')} fields, {summary.get('needsReview')} for review, "
        f"result.json schema v{stored.get('schemaVersion')}, {seconds}"
    )
    step("extraction end to end", True, detail)


def check_conversion() -> None:
    item, job, file_id = uuid4(), uuid4(), uuid4()
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 220, 240)).save(buffer, format="PNG")
    key = f"{ACCOUNT}/{file_id}/stack-check.png"
    s3().put_object(Bucket="idp-originals", Key=key, Body=buffer.getvalue(), ContentType="image/png")
    payload = {
        "jobId": str(job),
        "itemId": str(item),
        "type": "CONVERSION",
        "inputs": [{"bucket": "idp-originals", "key": key, "contentType": "image/png"}],
        "target": "PDF",
        "options": {},
        "outputPrefix": f"{ACCOUNT}/{job}/{item}",
    }
    body, event_id = envelope("ConversionRequested", item, payload)
    try:
        event = asyncio.run(
            send_and_wait("conversion.requested.standard", body, event_id, item, {"ConversionCompleted", "WorkFailed"})
        )
    except TimeoutError:
        step("conversion end to end", False, f"no answer in {TIMEOUT_S} s: is conversion-worker running?")
        return
    outputs = event.get("payload", {}).get("outputs", [])
    passed = event.get("type") == "ConversionCompleted" and bool(outputs)
    name = outputs[0]["key"].rsplit("/", 1)[-1] if passed else ""
    detail = f"{name}, {outputs[0].get('pageCount')} page" if passed else str(event)[:200]
    step("conversion end to end", passed, detail)


if __name__ == "__main__":
    check_health()
    check_api()
    check_extraction()
    check_conversion()
    failed = [name for name, passed, _ in results if not passed]
    summary = f"\n{len(results) - len(failed)} of {len(results)} checks passed"
    print(summary + (f"; failed: {', '.join(failed)}" if failed else ""))  # noqa: T201
    sys.exit(1 if failed else 0)
