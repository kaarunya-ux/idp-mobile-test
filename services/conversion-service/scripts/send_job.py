"""Play document-service (Java) against the local stack: send one job, watch it, download what it made.

Start the stack first (``make up``), then for example::

    uv run python scripts/send_job.py report.docx --to PDF
    uv run python scripts/send_job.py scan.pdf photo.jpg --type PDF_MERGE
    uv run python scripts/send_job.py contract.pdf --type PREVIEW
    uv run python scripts/send_job.py contract.pdf --type PDF_EDIT --plan plan.json
    uv run python scripts/send_job.py result1.json result2.json --type EXPORT --to XLSX

It uploads the files to MinIO as Java would, sends ``ConversionRequested`` to ``idp.commands``, prints every event
for the item, and saves the outputs in ``--out`` (default ``./job-output``). A manual tool, not part of the service;
the defaults match ``compose.yaml``.
"""

import argparse
import asyncio
import json
import mimetypes
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aio_pika
import boto3

_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".json": "application/json",
}
_FINAL = {"ConversionCompleted", "WorkFailed"}


def main() -> None:
    args = _arguments()
    s3 = boto3.client(
        "s3",
        endpoint_url=args.minio,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        region_name="us-east-1",
    )
    account, job, item = "local-account", uuid4(), uuid4()
    inputs = [_upload(s3, path, account) for path in args.files]
    options: dict[str, Any] = json.loads(args.options) if args.options else {}
    if args.plan:
        options["editPlan"] = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    payload = {
        "jobId": str(job),
        "itemId": str(item),
        "type": args.type,
        "inputs": inputs,
        "target": args.to,
        "options": options,
        "outputPrefix": f"{account}/{job}/{item}",
    }
    outputs = asyncio.run(_send_and_wait(args.rabbit, payload, item, args.timeout))
    args.out.mkdir(parents=True, exist_ok=True)
    for output in outputs:
        destination = args.out / output["key"].rsplit("/", 1)[-1]
        s3.download_file(output["bucket"], output["key"], str(destination))
        sys.stdout.write(f"saved {destination} ({output['size']:,} bytes, {output.get('pageCount')} pages)\n")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--type", default="CONVERSION", choices=["CONVERSION", "PDF_MERGE", "PREVIEW", "PDF_EDIT", "EXPORT"]
    )
    parser.add_argument("--to", help="target format, for CONVERSION and EXPORT (PDF, DOCX, XLSX, CSV, PPTX, PNG, ...)")
    parser.add_argument("--options", help="job options as JSON, for example '{\"dpi\": 200}'")
    parser.add_argument("--plan", help="a PDF_EDIT plan as a JSON file")
    parser.add_argument("--out", type=Path, default=Path("job-output"))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--minio", default="http://localhost:9000")
    parser.add_argument("--access-key", default="local-access-key")
    parser.add_argument("--secret-key", default="local-secret-key")
    parser.add_argument("--rabbit", default="amqp://guest:guest@localhost:5672/")
    return parser.parse_args()


def _upload(s3: Any, path: Path, account: str) -> dict[str, str]:
    content_type = (
        _CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    key = f"{account}/{uuid4()}/{path.name}"  # <accountId>/<documentId>/<fileId>, as IDP-00 ch. 7.7
    s3.upload_file(str(path), "idp-originals", key, ExtraArgs={"ContentType": content_type})
    return {"bucket": "idp-originals", "key": key, "contentType": content_type}


async def _send_and_wait(url: str, payload: dict[str, Any], item: Any, seconds: float) -> list[dict[str, Any]]:
    connection = await aio_pika.connect(url)
    async with connection:
        channel = await connection.channel()
        events = await channel.declare_exchange("idp.events", aio_pika.ExchangeType.TOPIC, durable=True)
        queue = await channel.declare_queue("", exclusive=True)
        await queue.bind(events, "#")
        commands = await channel.declare_exchange("idp.commands", aio_pika.ExchangeType.TOPIC, durable=True)
        event_id = uuid4()
        envelope = {
            "messageId": str(event_id),
            "type": "ConversionRequested",
            "version": 1,
            "occurredAt": datetime.now(UTC).isoformat(),
            "producer": "document-service",
            "correlationId": f"send-job-{item}",
            "tenantId": str(uuid4()),
            "subjectId": str(item),
            "payload": payload,
        }
        message = aio_pika.Message(json.dumps(envelope).encode(), headers={"eventId": str(event_id)}, delivery_mode=2)
        await commands.publish(message, routing_key="conversion.requested.standard")
        sys.stdout.write(f"sent {payload['type']} for item {item}\n")
        return await _watch(queue, str(item), seconds)


async def _watch(queue: aio_pika.abc.AbstractQueue, item: str, seconds: float) -> list[dict[str, Any]]:
    async with asyncio.timeout(seconds), queue.iterator() as messages:
        async for message in messages:
            async with message.process():
                event = json.loads(message.body)
                if event.get("subjectId") != item:
                    continue
                body = event["payload"]
                sys.stdout.write(
                    f"{event['type']}: {json.dumps({k: v for k, v in body.items() if k not in ('jobId', 'itemId')})}\n"
                )
                if event["type"] in _FINAL:
                    return list(body.get("outputs", []))
    return []


if __name__ == "__main__":
    main()
