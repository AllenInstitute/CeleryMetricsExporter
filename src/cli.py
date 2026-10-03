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
        "Broker visibility timeout (seconds) for this exporter's own connection. "
        "Must be at least as long as the longest task any worker on this broker runs: "
        "the exporter consumes the event queue, and a consuming kombu channel restores "
        "ANY unacknowledged message older than its own visibility timeout, across every "
        "queue on the broker. Too low and the exporter requeues other workers' running "
        "tasks."
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
    "-r",
    "--refresh",
    type=int,
    default=30,
    show_default=True,
    help="How often to refresh metrics in seconds",
)


def cli(broker_url: str, port: int, queue_names: List[str], refresh: int, visibility_timeout: int, unacked_keys: List[str]):
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
    )
    exporter.run()


