"""``MetricsPort`` on prometheus-client (IDP-03 ch. 4.6, OBS-06). Served at ``/metrics`` by ``main``."""

from prometheus_client import Counter, Gauge, Histogram

from ai_gateway.ai.domain.models import Priority, Tokens

REQUESTS = Counter("ai_requests_total", "Gateway model calls", ["task", "model", "outcome"])
LATENCY = Histogram(
    "ai_latency_seconds", "Model call time, repair included", ["task", "model"], buckets=(0.5, 1, 2, 5, 10, 20, 45, 90)
)
QUEUE_WAIT = Histogram(
    "ai_queue_wait_seconds", "Time waiting for a model slot", ["priority"], buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 20)
)
TOKENS = Counter("ai_tokens_total", "Tokens sent and received", ["model", "direction"])
BUILD_INFO = Gauge(
    "ai_build_info", "The running build: always 1; the labels say which", ["env", "version", "commit", "config_hash"]
)


def publish_build_info(*, env: str, version: str, commit: str, config_hash: str) -> None:
    """One series per build, so a dashboard can mark a deploy or a configuration change on any other metric."""
    BUILD_INFO.labels(env=env, version=version, commit=commit, config_hash=config_hash).set(1)


class PrometheusMetrics:
    def call_finished(self, *, task: str, model: str, outcome: str, latency_s: float | None, tokens: Tokens) -> None:
        REQUESTS.labels(task=task, model=model, outcome=outcome).inc()
        if latency_s is not None:
            LATENCY.labels(task=task, model=model).observe(latency_s)
        TOKENS.labels(model=model, direction="in").inc(tokens.input)
        TOKENS.labels(model=model, direction="out").inc(tokens.output)

    def waited(self, *, priority: Priority, seconds: float) -> None:
        QUEUE_WAIT.labels(priority=priority.value).observe(seconds)
