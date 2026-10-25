import click
from typing import List
from src.exporter import CeleryMetricsExporter

@click.command(context_settings={"auto_envvar_prefix": "CELERY_EXPORTER"})

@click.option(
    "--broker-url", required=True, help="The url to the broker, e.g redis://1.2.3.4"
)
@click.option(
    "--port",
    type=int,
    default=9540,
    show_default=True,
    help="The port the exporter will listen on",
)
@click.option(
    "-q",
    "--queue-names",
    multiple=True,
    required=True,
    help="The queues to track",
)
@click.option(
    "--visibility-timeout",
    type=int,
    default=21600,
    show_default=True,
    help=(
        "Broker visibility timeout (seconds) for this exporter's connections. With "
        "--restore-stale-unacked, messages unacknowledged for longer than this are "
        "requeued, so it must be at least as long as the longest task any worker on this "
        "broker runs; too low and the exporter requeues other workers' running tasks. "
        "(The event receiver alone never restores anything: on redis it reads events by "
        "pub/sub, not by consuming a queue.)"
    ),
)
@click.option(
    "--unacked-key",
    "unacked_keys",
    multiple=True,
    default=("unacked",),
    show_default=True,
    help=(
        "Broker hash(es) holding unacknowledged (in-flight) messages; repeat for each. "
        "Workers given their own redis transport unacked_key (e.g. to use a shorter "
        "visibility_timeout) must be listed here, or their in-flight tasks read as zero."
    ),
)
@click.option(
    "--restore-stale-unacked/--no-restore-stale-unacked",
    default=False,
    show_default=True,
    help=(
        "Requeue messages left in the default unacked hash for longer than "
        "--visibility-timeout, i.e. claimed by a worker that died. kombu does this only "
        "from a worker consuming a queue, so with every worker scaled to zero nothing "
        "else would. --visibility-timeout must then exceed the longest task."
    ),
)
@click.option(
    "-r",
    "--refresh",
    type=int,
    default=30,
    show_default=True,
    help="How often to refresh metrics in seconds",
)


def cli(broker_url: str, port: int, queue_names: List[str], refresh: int, visibility_timeout: int, unacked_keys: List[str], restore_stale_unacked: bool):
    ctx = click.get_current_context()
    params = ctx.params
    broker_url = params["broker_url"]
    port = params["port"]
    queue_names = params["queue_names"]
    metrics_refresh = params["refresh"]
    visibility_timeout = params["visibility_timeout"]
    exporter = CeleryMetricsExporter(
        broker_url, queue_names, port, metrics_refresh, visibility_timeout,
        unacked_keys=params["unacked_keys"],
        restore_stale_unacked=params["restore_stale_unacked"],
    )
    exporter.run()


