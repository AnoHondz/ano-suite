"""Single-worker deployment; scheduler starts after worker initialization."""

import os

workers = 1
threads = 4
timeout = 120
preload_app = False
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Keep gunicorn's access log quiet unless DEBUG is set
accesslog = None if not os.environ.get("DEBUG") else "-"
errorlog = "-"
loglevel = "info"


def post_worker_init(worker):
    from app import start_background_services
    start_background_services()


def worker_exit(server, worker):
    from app import stop_background_services
    stop_background_services()
