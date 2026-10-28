"""``MetricsPort`` on prometheus-client (IDP-03 ch. 4.6, OBS-06). Served at ``/metrics`` by ``main``."""

from prometheus_client import Counter, Histogram

from ai_gateway.ai.domain.models import Priority, Tokens

REQUESTS = Counter("ai_requests_total", "Gateway model calls", ["task", "model", "outcome"])
LATENCY = Histogram(
    "ai_latency_seconds", "Model call time, repair included", ["task", "model"], buckets=(0.5, 1, 2, 5, 10, 20, 45, 90)
)
QUEUE_WAIT = Histogram(
    "ai_queue_wait_seconds", "Time waiting for a model slot", ["priority"], buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 20)
)
TOKENS = Counter("ai_tokens_total", "Tokens sent and received", ["model", "direction"])


class PrometheusMetrics:
    def call_finished(self, *, task: str, model: str, outcome: str, latency_s: float | None, tokens: Tokens) -> None:
        REQUESTS.labels(task=task, model=model, outcome=outcome).inc()
        if latency_s is not None:
            LATENCY.labels(task=task, model=model).observe(latency_s)
        TOKENS.labels(model=model, direction="in").inc(tokens.input)
        TOKENS.labels(model=model, direction="out").inc(tokens.output)

    def waited(self, *, priority: Priority, seconds: float) -> None:
        QUEUE_WAIT.labels(priority=priority.value).observe(seconds)
