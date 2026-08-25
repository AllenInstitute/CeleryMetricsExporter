import json

from prometheus_client import CollectorRegistry, Gauge

from src.exporter import QueueLengthMonitoringThread


class _FakeClient:
    """Stands in for the redis client the thread now holds directly.

    The thread used to reach its client through celery_app.connection().channel(), so the
    fakes here mirrored that chain. It now keeps a plain redis client, both to stop
    leaking a connection per scrape and so that it holds no consuming kombu channel --
    a consuming channel restores unacknowledged messages broker-wide, which is how a
    monitoring process ends up requeueing another worker's running task.
    """

    def __init__(self, unacked, lengths=None):
        self._unacked = unacked
        self._lengths = lengths or {}

    def hgetall(self, key):
        assert key == "unacked"
        return self._unacked

    def llen(self, queue):
        return self._lengths.get(queue, 0)


def _unacked_entry(routing_key):
    # kombu stores each unacked delivery tag -> [message, exchange, routing_key]
    return json.dumps([{"body": ""}, "", routing_key])


def _make_thread(unacked, queues):
    # Bypass __init__ (it builds a real broker connection); set only what
    # measure_inflight needs.
    thread = QueueLengthMonitoringThread.__new__(QueueLengthMonitoringThread)
    thread._redis = _FakeClient(unacked)
    thread.queue_list = queues
    registry = CollectorRegistry()
    thread.inflight_metric = Gauge(
        "celery_inflight_tasks", "test", ["queue_name"], registry=registry
    )
    return thread


def test_measure_inflight_buckets_by_routing_key():
    unacked = {
        b"tag1": _unacked_entry("workflow"),
        b"tag2": _unacked_entry("workflow"),
        b"tag3": _unacked_entry("orchestration"),
        b"tag4": _unacked_entry("process"),
    }
    thread = _make_thread(unacked, ["workflow", "process", "orchestration", "deltalake"])
    thread.measure_inflight()

    m = thread.inflight_metric
    assert m.labels("workflow")._value.get() == 2
    assert m.labels("orchestration")._value.get() == 1
    assert m.labels("process")._value.get() == 1
    # a tracked queue with no in-flight work must report 0, not stay unset
    assert m.labels("deltalake")._value.get() == 0


def test_measure_inflight_ignores_untracked_and_bad_entries():
    unacked = {
        b"tag1": _unacked_entry("some_other_app_queue"),  # not tracked -> ignored
        b"tag2": b"not-json",                              # unparseable -> skipped
        b"tag3": _unacked_entry("workflow"),
    }
    thread = _make_thread(unacked, ["workflow"])
    thread.measure_inflight()

    assert thread.inflight_metric.labels("workflow")._value.get() == 1


def test_measure_inflight_noop_without_metric():
    thread = QueueLengthMonitoringThread.__new__(QueueLengthMonitoringThread)
    thread.inflight_metric = None
    thread.queue_list = ["workflow"]
    # must not raise, and must not touch the broker
    thread.measure_inflight()


def test_metrics_thread_holds_no_kombu_channel():
    """The thread must reach redis directly, never through a kombu channel.

    A consuming kombu channel drives maybe_restore_messages, which restores ANY
    unacknowledged message older than that connection's visibility_timeout -- across every
    queue on the broker, not just the ones being monitored. A monitoring process that
    holds one can silently requeue another worker's still-running task.
    """
    import ast
    import inspect

    from src import exporter

    src = inspect.getsource(exporter)
    cls = next(
        n for n in ast.parse(src).body
        if isinstance(n, ast.ClassDef) and n.name == "QueueLengthMonitoringThread"
    )
    called = {
        n.func.attr for n in ast.walk(cls)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not called & {"channel", "connection", "connection_or_acquire"}, called


def test_exporter_sets_a_visibility_timeout():
    """Left at kombu's 3600s default, this exporter's own event-receiver connection
    reclaims long-running tasks belonging to other workers."""
    import inspect

    from src.exporter import CeleryMetricsExporter

    sig = inspect.signature(CeleryMetricsExporter.__init__)
    assert "visibility_timeout" in sig.parameters
    assert sig.parameters["visibility_timeout"].default >= 21600
