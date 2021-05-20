# Celery Metrics Exporter

A simple web server to expose celery metrics with a Prometheus client.

Heavily inspired by both https://github.com/OvalMoney/celery-exporter and
https://github.com/danihodovic/celery-exporter

## Usage

To run using docker:

```
$ docker run -p 9540:9540 exporter --port=9540 --broker-url=redis:// --queue-names=process

```