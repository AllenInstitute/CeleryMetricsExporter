import json

from prometheus_client import CollectorRegistry, Gauge

from src.exporter import QueueLengthMonitoringThread


class _FakeClient:
    def __init__(self, unacked):
        self._unacked = unacked

    def hgetall(self, key):
        assert key == "unacked"
        return self._unacked


class _FakeChannel:
    def __init__(self, client):
        self.client = client


class _FakeConnection:
    def __init__(self, client):
        self._client = client

    def channel(self):
        return _FakeChannel(self._client)


class _FakeCeleryApp:
    def __init__(self, client):
        self._client = client

    def connection(self):
        return _FakeConnection(self._client)


def _unacked_entry(routing_key):
    # kombu stores each unacked delivery tag -> [message, exchange, routing_key]
    return json.dumps([{"body": ""}, "", routing_key])


def _make_thread(unacked, queues):
    # Bypass __init__ (it builds a real broker connection); set only what
    # measure_inflight needs.
    thread = QueueLengthMonitoringThread.__new__(QueueLengthMonitoringThread)
    thread.celery_app = _FakeCeleryApp(_FakeClient(unacked))
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
