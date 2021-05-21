from threading import Thread
import logging
from flask import Blueprint, Flask, current_app, request
from prometheus_client.exposition import choose_encoder
from waitress import serve
from prometheus_client import make_wsgi_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware

bp = Blueprint("celery_exporter", __name__)


@bp.route("/metrics")
def metrics():
    encoder, content_type = choose_encoder(request.headers.get("accept"))
    output = encoder(current_app.config["metrics"])
    return output, 200, {"Content-Type": content_type}


def create_app(celery, metrics, port):

    app = Flask(__name__)
    app.config["metrics"] = metrics
    app.config["celery"] = celery
    app.register_blueprint(bp)

    @app.route("/health")
    def health():
        conn = current_app.config["celery"]
        uri = conn.as_uri()

        try:
            conn.ensure_connection(max_retries=3)
        except Exception as e:
            logging.error("Failed to connect to broker='{}'", uri)
            return (f"Failed to connect to broker: '{uri}'", 500)
        return f"Connected to the celery broker {conn.as_uri()}"

    Thread(
        target=serve,
        args=(app,),
        kwargs=dict(host="0.0.0.0", port=port, _quiet=False),
        daemon=True,
    ).start()

    logging.info(f"Started exporter")
