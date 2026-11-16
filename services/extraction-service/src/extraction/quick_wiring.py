"""Wiring for the ``extraction-sync`` process: the synchronous extract (ADR 0009) with template detection for
``/extract/auto`` (ADR 0016). Its own module, as ``api_wiring`` is for the API: the composition root (``bootstrap``)
builds the pipeline it runs on.
"""

import httpx

from extraction.bootstrap import PipelineServices, required_service_auth
from extraction.core.clock import SystemClock
from extraction.core.config import MissingSettingsError, Settings
from extraction.core.db import SessionFactory
from extraction.core.service_auth import ServiceTokenProvider
from extraction.pipeline.infrastructure.ai.gateway_reader import GatewayClassifier
from extraction.pipeline.infrastructure.ai.inline_images import BUCKET as INLINE_BUCKET
from extraction.pipeline.infrastructure.ai.inline_images import InlineImages
from extraction.pipeline.infrastructure.persistence.template_catalog import SqlTemplateCatalog
from extraction.quick.application.quick_extract import QuickExtractService
from extraction.quick.application.template_matcher import TemplateMatcher
from extraction.quick.domain.models import GenericMode, UploadLimits
from extraction.quick.infrastructure.gateway_reader import GatewayDocumentReader
from extraction.quick.infrastructure.page_words import OcrPageWords
from extraction.quick.infrastructure.pipeline_runner import PipelineDocumentRunner
from extraction.quick.infrastructure.templates import CatalogTemplates, GatewayTemplateClassifier
from extraction.quick.infrastructure.workspace import MemoryWorkspace


def build_quick_extract(settings: Settings, sessions: SessionFactory) -> QuickExtractService:
    """The pipeline with its OCR engines (loaded here, once), and ai-gateway for the crops, the generic read and
    classify. No storage (ADR 0012): the request's pages and crops are held in memory and sent inside the gateway
    calls."""
    gateway = settings.ai_gateway
    if gateway.url is None:
        raise MissingSettingsError("extraction-sync needs AI_GATEWAY__URL")
    auth = required_service_auth(settings)
    inline = InlineImages()
    pipeline = PipelineServices(settings, SystemClock(), SqlTemplateCatalog(sessions), pages=inline, inline=inline)
    tokens = ServiceTokenProvider(httpx.Client(timeout=auth.timeout.total_seconds()), auth)
    seconds = settings.quick.timeout.total_seconds()
    client = httpx.Client(base_url=str(gateway.url).rstrip("/"), timeout=httpx.Timeout(seconds, connect=2.0))
    quick = settings.quick
    classifier = GatewayTemplateClassifier(GatewayClassifier(pipeline.gateway)) if pipeline.gateway else None
    return QuickExtractService(
        workspace=MemoryWorkspace(inline),
        pipeline=PipelineDocumentRunner(
            pipeline, pipeline.loader, inline, bucket=INLINE_BUCKET, max_pages=quick.max_pages
        ),
        reader=GatewayDocumentReader(client, tokens, inline),
        words=OcrPageWords(pipeline.loader, pipeline.sample_engine),
        generic_mode=GenericMode(quick.generic_mode),
        limits=UploadLimits(max_file_bytes=int(quick.max_file_size), max_total_bytes=int(quick.max_total_size)),
        matcher=TemplateMatcher(
            templates=CatalogTemplates(pipeline.catalog),
            classifier=classifier,
            minimum=quick.detect_min_confidence,
            min_filled=quick.detect_min_filled,
        ),
    )
