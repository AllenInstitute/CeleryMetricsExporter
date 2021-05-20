from src.cli import cli

from click.testing import CliRunner


def test_main_cli():
    runner = CliRunner()
    result = runner.invoke(cli, ["redis://redis:6379/0", 9808, ['celery']])
    assert result.exit_code == 0
    assert {"broker-url": "redis://redis:6379/0",
            "port": 9808,
            "queue-name": "celery"} in result.output