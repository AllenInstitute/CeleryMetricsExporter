import json
import logging
import threading
import time
from typing import List

import celery
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
                       metrics_refresh: int=30):
        
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
        self.connection = self.celery_app.connection_or_acquire()
        if isinstance(self.connection, FallbackContext):
                    self.connection = self.connection.fallback()
        self.queues = queue_names
        self.port = port
        self.metrics_refresh = metrics_refresh
        logging.info("starting")

 
    def run(self):

        logging.info("running")
      
        with self.celery_app.connection() as connection:
            app = create_app(connection, self.registry, self.port)
           
            q = QueueLengthMonitoringThread(app=self.celery_app, queue_list=self.queues, queue_metric=self.queue_length, inflight_metric=self.inflight_tasks, metrics_refresh=self.metrics_refresh)
            q.daemon = True
            q.start()

            handlers = {}

            recv = self.celery_app.events.Receiver(connection, handlers=handlers)
            recv.capture(limit=None, timeout=None, wakeup=True)


class QueueLengthMonitoringThread(threading.Thread):

    def __init__(self, app: celery, queue_list: List[str], queue_metric: str, inflight_metric=None, metrics_refresh: int=30):
        self.queue_metric = queue_metric
        self.inflight_metric = inflight_metric
        self.celery_app = app
        self.queue_list = queue_list
        self.metrics_refresh = metrics_refresh
        self.connection = self.celery_app.connection_or_acquire()

        if isinstance(self.connection, FallbackContext):
            self.connection = self.connection.fallback()

        super(QueueLengthMonitoringThread, self).__init__()

    def measure_queues_length(self):
        for queue in self.queue_list:
            try:
                client = self.celery_app.connection().channel().client
                length = client.llen(queue)
            except Exception as e:
                logging.warning("Queue Not Found: {}. Setting its value to zero. Error: {}".format(queue, str(e)))
                length = 0

            self.set_queue_length(queue, length)

    def set_queue_length(self, queue, length):
        self.queue_metric.labels(queue).set(length)

    def measure_inflight(self):
        """Count in-flight (received but unacknowledged) tasks per queue.

        The broker's ``unacked`` hash maps each delivery tag to
        ``[message, exchange, routing_key]``; the routing key is the queue name.
        We read it once and bucket by queue so a busy pool (queue_length == 0 but
        a task still running) is still visible to the autoscaler.
        """
        if self.inflight_metric is None:
            return

        counts = {queue: 0 for queue in self.queue_list}
        try:
            client = self.celery_app.connection().channel().client
            unacked = client.hgetall("unacked")
        except Exception as e:
            logging.warning("Could not read unacked hash, setting inflight to zero. Error: {}".format(str(e)))
            unacked = {}

        for _tag, value in (unacked or {}).items():
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

