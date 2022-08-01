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
           
            q = QueueLengthMonitoringThread(app=self.celery_app, queue_list=self.queues, queue_metric=self.queue_length, metrics_refresh=self.metrics_refresh)
            q.daemon = True
            q.start()

            handlers = {}

            recv = self.celery_app.events.Receiver(connection, handlers=handlers)
            recv.capture(limit=None, timeout=None, wakeup=True)


class QueueLengthMonitoringThread(threading.Thread):

    def __init__(self, app: celery, queue_list: List[str], queue_metric: str, metrics_refresh: int=30):
        self.queue_metric = queue_metric
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

    def run(self):  # pragma: no cover
        while True:
            self.measure_queues_length()
            time.sleep(self.metrics_refresh)

