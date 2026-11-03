"""Loaded automatically from Gunicorn's existing working directory."""

import faulthandler


# Stagger worker recycling instead of letting a worker grow indefinitely.
# Existing command-line worker/thread counts and long-job timeouts still apply.
max_requests = 2000
max_requests_jitter = 200
capture_output = True


def on_starting(server):
    # Enable before fork so even a crash before worker initialization is logged.
    faulthandler.enable(all_threads=True)


def on_reload(server):
    faulthandler.enable(all_threads=True)


def post_fork(server, worker):
    faulthandler.enable(all_threads=True)
