import logging
import threading
import time

import celery
from celery.utils.objects import FallbackContext
from prometheus_client import CollectorRegistry, Counter, Gauge

from src.app import create_app
from typing import List

logging.basicConfig(level=logging.INFO)




class CeleryMetricsExporter:
    """
    """

    def __init__(self, broker_url: str, 
                       queue_names: List[str],
                       port: int=9808):
        
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
        self.connection = self.celery_app.connection_or_acquire()
        if isinstance(self.connection, FallbackContext):
                    self.connection = self.connection.fallback()
        self.queues = queue_names
        self.port = port
        logging.info("starting")

    def measure_queues_length(self):
        for queue in self.queues:
            try:
                length = self.connection.default_channel.queue_declare(queue=queue, passive=True).message_count
            except Exception as e:
                logging.warning("Queue Not Found: {}. Setting its value to zero. Error: {}".format(queue, str(e)))
                length = 0

            self.set_queue_length(queue, length)

    def set_queue_length(self, queue, length):
        self.queue_length.labels(queue).set(length)
 
    def run(self):
        handlers = {
            'queue-length': self.measure_queues_length
        }
        with self.celery_app.connection() as connection:
            app = create_app(connection, self.registry, self.port)
            
            recv = self.celery_app.events.Receiver(connection, handlers=handlers)
            recv.capture(limit=None, timeout=None, wakeup=True)


    