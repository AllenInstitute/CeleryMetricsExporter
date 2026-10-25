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

    def __init__(self, unacked, lengths=None, other_hashes=None):
        self._hashes = {"unacked": unacked, **(other_hashes or {})}
        self._lengths = lengths or {}

    def hgetall(self, key):
        return self._hashes.get(key, {})

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


def test_measure_inflight_reads_only_the_default_hash_by_default():
    thread = _make_thread({b"tag1": _unacked_entry("process")}, ["process"])
    thread._redis = _FakeClient(
        {b"tag1": _unacked_entry("process")},
        other_hashes={"process_unacked": {b"tag2": _unacked_entry("process")}},
    )
    thread.measure_inflight()

    assert thread.inflight_metric.labels("process")._value.get() == 1


def test_measure_inflight_sums_every_configured_unacked_hash():
    """A pool given its own unacked_key (to use a shorter visibility_timeout) keeps its
    in-flight tasks in a separate hash; they must still count, or the autoscaler stops
    its pods mid-task."""
    thread = _make_thread({b"tag1": _unacked_entry("workflow")}, ["workflow", "process"])
    thread._redis = _FakeClient(
        {b"tag1": _unacked_entry("workflow")},
        other_hashes={
            "process_unacked": {
                b"tag2": _unacked_entry("process"),
                b"tag3": _unacked_entry("process"),
            }
        },
    )
    thread.unacked_keys = ("unacked", "process_unacked")
    thread.measure_inflight()

    assert thread.inflight_metric.labels("workflow")._value.get() == 1
    assert thread.inflight_metric.labels("process")._value.get() == 2


def test_cli_accepts_repeated_unacked_keys():
    from click.testing import CliRunner
    from unittest import mock

    from src import cli as cli_module

    with mock.patch.object(cli_module, "CeleryMetricsExporter") as exporter:
        result = CliRunner().invoke(
            cli_module.cli,
            ["--broker-url", "redis://x", "-q", "process",
             "--unacked-key", "unacked", "--unacked-key", "process_unacked"],
        )
    assert result.exit_code == 0, result.output
    assert exporter.call_args.kwargs["unacked_keys"] == ("unacked", "process_unacked")


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


# --- StaleUnackedRestorer: runs against a real redis (REDIS_TEST_URL), skipped without one

import os
import time
from unittest import mock

import pytest

REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6390/15")


def _redis_or_skip():
    import redis

    client = redis.Redis.from_url(REDIS_TEST_URL)
    try:
        client.ping()
    except Exception:
        pytest.skip(f"no redis at {REDIS_TEST_URL}")
    client.flushdb()
    return client


def _claim(client, tag, queue, task_id, claimed_at):
    """Put a message in the unacked hash as kombu does when a worker receives it."""
    payload = {
        "body": "", "content-encoding": "utf-8", "content-type": "application/json",
        "headers": {"id": task_id, "task": "orchestration:upload_to_db"},
        "properties": {"delivery_tag": tag, "delivery_info": {"exchange": "", "routing_key": queue}},
    }
    client.hset("unacked", tag, json.dumps([payload, "", queue]))
    client.zadd("unacked_index", {tag: claimed_at})


def test_restorer_requeues_only_messages_older_than_the_visibility_timeout():
    from src.exporter import StaleUnackedRestorer

    client = _redis_or_skip()
    now = time.time()
    _claim(client, "dead", "orchestration", "task-of-a-dead-pod", now - 1000)
    _claim(client, "alive", "orchestration", "task-still-running", now - 100)

    restorer = StaleUnackedRestorer(REDIS_TEST_URL, visibility_timeout=900)
    assert restorer.stale_count() == 1
    assert restorer.restore() == 1

    queued = [json.loads(m) for m in client.lrange("orchestration", 0, -1)]
    assert [m["headers"]["id"] for m in queued] == ["task-of-a-dead-pod"]
    assert client.hkeys("unacked") == [b"alive"]  # the running task stays claimed
    assert [t for t, _ in client.zrange("unacked_index", 0, -1, withscores=True)] == [b"alive"]
    assert restorer.restore() == 0  # nothing left to do
    client.flushdb()


def test_restorer_is_a_noop_without_stale_messages():
    from src.exporter import StaleUnackedRestorer

    client = _redis_or_skip()
    _claim(client, "alive", "workflow", "running", time.time())
    assert StaleUnackedRestorer(REDIS_TEST_URL, visibility_timeout=900).restore() == 0
    assert client.llen("workflow") == 0
    client.flushdb()


def test_cli_restore_flag_defaults_off():
    from click.testing import CliRunner

    from src import cli as cli_module

    with mock.patch.object(cli_module, "CeleryMetricsExporter") as exporter:
        result = CliRunner().invoke(cli_module.cli, ["--broker-url", "redis://x", "-q", "orchestration"])
        assert result.exit_code == 0, result.output
        assert exporter.call_args.kwargs["restore_stale_unacked"] is False
        CliRunner().invoke(cli_module.cli, ["--broker-url", "redis://x", "-q", "orchestration", "--restore-stale-unacked"])
        assert exporter.call_args.kwargs["restore_stale_unacked"] is True
