"""A PREVIEW on real PostgreSQL and MinIO, wired as production wires it: thumbnails land in the derived bucket."""

from io import BytesIO
from uuid import uuid4

import img2pdf
from mypy_boto3_s3 import S3Client
from PIL import Image

from conversion.bootstrap import build_process_service, build_units
from conversion.conversion.application.commands import ProcessItemCommand
from conversion.conversion.domain.events import ItemContext
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest, ItemStatus
from conversion.conversion.domain.values import InputFile, JobType, ObjectRef
from conversion.core.clock import SystemClock
from conversion.core.config import Settings


def _two_page_pdf() -> bytes:
    pages = []
    for size in ((595, 842), (842, 595)):
        page = BytesIO()
        Image.new("RGB", size, "white").save(page, format="PNG", dpi=(72, 72))
        pages.append(page.getvalue())
    return img2pdf.convert(pages)


def test_writes_page_thumbnails_to_the_derived_bucket_when_a_preview_runs(
    settings: Settings, sessions, s3: S3Client
) -> None:
    s3.put_object(Bucket="idp-originals", Key="acc/doc/two-pages", Body=_two_page_pdf())
    context = ItemContext(tenant_id=uuid4(), correlation_id="corr", job_id=uuid4(), item_id=uuid4())
    request = ConversionRequest(
        context=context,
        inputs=(InputFile(ref=ObjectRef(bucket="idp-originals", key="acc/doc/two-pages"), format=Format.PDF),),
        target=Format.JPEG,
        options={},
        output_prefix=f"acc/doc/{context.item_id}",
        job_type=JobType.PREVIEW,
    )
    with build_units(sessions, SystemClock())() as unit:
        unit.items.add(ConversionItem.receive(request, SystemClock().now()))
        unit.commit()

    build_process_service(settings, sessions).process(ProcessItemCommand(item_id=context.item_id))

    with build_units(sessions, SystemClock())() as unit:
        item = unit.items.get_for_update(context.item_id)
    assert item is not None
    assert item.status is ItemStatus.COMPLETED
    assert item.completion is not None
    sizes = []
    for output in item.completion.outputs:
        assert output.ref.bucket == "idp-derived"
        body = s3.get_object(Bucket=output.ref.bucket, Key=output.ref.key)["Body"].read()
        with Image.open(BytesIO(body)) as image:
            sizes.append(image.size)
    assert sizes == [(283, 400), (400, 283)]
