import json
import logging
import threading
import time
from typing import List, Sequence

import celery
import redis as redis_lib
from celery.utils.objects import FallbackContext
from prometheus_client import CollectorRegistry, Counter, Gauge

from src.app import create_app

logging.basicConfig(level=logging.INFO)




class CeleryMetricsExporter:
    """
    """

    def __init__(self, broker_url: str,
                       queue_names: List[str],
                       port: int=9808,
                       metrics_refresh: int=30,
                       visibility_timeout: int=21600,
                       unacked_keys: Sequence[str]=("unacked",)):

        self.registry = CollectorRegistry(auto_describe=True)

        # self.tasks = Counter(
        #     "celery_tasks_total", "Number of task events.",
        #     ["namespace", "name", "state", "queue"],

        # )

        # TASKS_FAILED = Counter(
        #     "celery_task_failed", "Sent if the execution of the task failed.",
        #     ["name", "hostname", "exception"],
        #     s
        # )

        # TASKS_REJECTED = Counter(
        #     "celery_task_rejected", "The task was rejected by the worker", 
        #     ["name", "hostname"]
        # )

        # TASKS_REVOKED = Counter(
        #     "celery_task_revoked", "Sent if the task has been revoked.", 
        #     ["name", "hostname"]
        # )

        # TASKS_RETRIED = Counter(
        #     "celery_task_retried",
        #     "Sent if the task failed, but will be retried in the future.",
        #     ["name", "hostname"],
        # )

        self.queue_length = Gauge(
            "celery_queue_length", "Number of tasks in the queue.",
            ["queue_name"],
            registry = self.registry
        )
        # In-flight (received but unacknowledged) tasks per queue. celery_queue_length
        # (LLEN) only counts messages still WAITING in the queue; once a worker
        # receives a task the broker moves it to the `unacked` hash, so a pool that
        # is busy shows queue_length == 0. Autoscalers that key only on queue_length
        # therefore scale a working pool to zero and kill/redeliver the in-flight
        # task. This gauge exposes that in-flight work so scaling can account for it.
        self.inflight_tasks = Gauge(
            "celery_inflight_tasks",
            "Number of received-but-unacknowledged (in-flight) tasks per queue.",
            ["queue_name"],
            registry = self.registry
        )
        self.celery_app = celery.Celery(broker_url=broker_url)
        self.celery_app.conf.broker_url = broker_url
        self.celery_app.conf.result_backend = broker_url
        # This app runs an event receiver (see run()), which consumes the celeryev queue.
        # A consuming channel drives kombu's maybe_restore_messages, and that restores ANY
        # unacknowledged message older than *this connection's* visibility_timeout -- the
        # unacked index is global to the broker, not per queue. Left at kombu's 3600s
        # default, a monitoring process silently requeues other workers' long-running
        # tasks: on 2026-08-25 a deltalake export was redelivered 3,677s after it started,
        # while still running, and the duplicate failed and took the original down with it.
        # This must be at least as long as the longest task any worker on this broker runs.
        self.celery_app.conf.broker_transport_options = {
            "visibility_timeout": visibility_timeout,
        }
        self.broker_url = broker_url
        self.connection = self.celery_app.connection_or_acquire()
        if isinstance(self.connection, FallbackContext):
                    self.connection = self.connection.fallback()
        self.queues = queue_names
        self.port = port
        self.metrics_refresh = metrics_refresh
        self.unacked_keys = tuple(unacked_keys)
        logging.info("starting")

 
    def run(self):

        logging.info("running")
      
        with self.celery_app.connection() as connection:
            app = create_app(connection, self.registry, self.port)
           
            q = QueueLengthMonitoringThread(app=self.celery_app, queue_list=self.queues, queue_metric=self.queue_length, inflight_metric=self.inflight_tasks, metrics_refresh=self.metrics_refresh, broker_url=self.broker_url, unacked_keys=self.unacked_keys)
            q.daemon = True
            q.start()

            handlers = {}

            recv = self.celery_app.events.Receiver(connection, handlers=handlers)
            recv.capture(limit=None, timeout=None, wakeup=True)


class QueueLengthMonitoringThread(threading.Thread):

    # kombu's default unacked hash. Workers can be given their own (the redis transport's
    # unacked_key option), e.g. so one pool can use a shorter visibility_timeout without
    # restoring other pools' long-running tasks; list every hash in use here, or tasks
    # in flight on such a pool read as zero and the autoscaler stops its pods mid-task.
    unacked_keys = ("unacked",)

    def __init__(self, app: celery, queue_list: List[str], queue_metric: str, inflight_metric=None, metrics_refresh: int=30, broker_url: str=None, unacked_keys: Sequence[str]=("unacked",)):
        self.unacked_keys = tuple(unacked_keys)
        self.queue_metric = queue_metric
        self.inflight_metric = inflight_metric
        self.celery_app = app
        self.queue_list = queue_list
        self.metrics_refresh = metrics_refresh
        # A plain redis client, built once, rather than a kombu Connection/Channel per
        # scrape. The previous code called celery_app.connection().channel() on every
        # measurement and never closed either, leaking a redis connection each time. All
        # this thread needs is LLEN and HGETALL, which the raw client does directly, and
        # holding no kombu channel keeps this thread incapable of participating in
        # message restoration at all.
        self._redis = redis_lib.Redis.from_url(broker_url or app.conf.broker_url)

        super(QueueLengthMonitoringThread, self).__init__()

    def measure_queues_length(self):
        for queue in self.queue_list:
            try:
                length = self._redis.llen(queue)
            except Exception as e:
                logging.warning("Queue Not Found: {}. Setting its value to zero. Error: {}".format(queue, str(e)))
                length = 0

            self.set_queue_length(queue, length)

    def set_queue_length(self, queue, length):
        self.queue_metric.labels(queue).set(length)

    def measure_inflight(self):
        """Count in-flight (received but unacknowledged) tasks per queue.

        Each unacked hash maps a delivery tag to ``[message, exchange, routing_key]``;
        the routing key is the queue name. We read every configured hash once and bucket
        by queue so a busy pool (queue_length == 0 but a task still running) is still
        visible to the autoscaler.
        """
        if self.inflight_metric is None:
            return

        counts = {queue: 0 for queue in self.queue_list}
        unacked = {}
        for key in self.unacked_keys:
            try:
                unacked.update(self._redis.hgetall(key) or {})
            except Exception as e:
                logging.warning("Could not read unacked hash {}, skipping it. Error: {}".format(key, str(e)))

        for _tag, value in unacked.items():
            try:
                message = json.loads(value)
                routing_key = message[2] if isinstance(message, list) and len(message) >= 3 else None
            except Exception:
                routing_key = None
            if routing_key in counts:
                counts[routing_key] += 1

        for queue, count in counts.items():
            self.inflight_metric.labels(queue).set(count)

    def run(self):  # pragma: no cover
        while True:
            self.measure_queues_length()
            self.measure_inflight()
            time.sleep(self.metrics_refresh)

