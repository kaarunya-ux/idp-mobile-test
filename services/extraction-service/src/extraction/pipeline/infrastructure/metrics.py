"""``PipelineMetricsPort`` on prometheus-client (IDP-03 ch. 2.12, OBS-06).

The Celery workers are several processes, so prometheus-client's multiprocess mode is used there: each process writes
to ``PROMETHEUS_MULTIPROC_DIR`` and the worker's main process serves the sum on ``METRICS__PORT`` (``worker.py``).
"""

from prometheus_client import Counter, Histogram

from extraction.pipeline.domain.runs import PipelineRun

STAGE_DURATION = Histogram(
    "extraction_stage_duration_seconds",
    "Time per pipeline stage",
    ["stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
FIELD_DECISIONS = Counter(
    "extraction_field_decisions_total", "Fields by how they were decided", ["source", "status", "document_type"]
)
QUALITY_REJECTIONS = Counter("extraction_quality_rejections_total", "Items refused by the quality gate", ["reason"])
AI_FALLBACK = Counter("extraction_ai_fallback_total", "Items that asked the AI", ["document_type"])
ITEMS_FAILED = Counter("extraction_items_failed_total", "Items that ended with an error", ["code"])
_QUALITY_PREFIX = "EXT-422-QUALITY-"


class PrometheusPipelineMetrics:
    def run_finished(self, run: PipelineRun) -> None:
        document_type = run.document_type or "UNKNOWN"
        for stage, milliseconds in run.stages.items():
            STAGE_DURATION.labels(stage=stage).observe(milliseconds / 1000)
        for decision, count in run.decisions.items():
            source, _, status = decision.partition("/")
            FIELD_DECISIONS.labels(source=source, status=status, document_type=document_type).inc(count)
        if run.ai_calls:
            AI_FALLBACK.labels(document_type=document_type).inc()

    def item_failed(self, code: str) -> None:
        ITEMS_FAILED.labels(code=code).inc()
        if code.startswith(_QUALITY_PREFIX):
            QUALITY_REJECTIONS.labels(reason=code).inc()
