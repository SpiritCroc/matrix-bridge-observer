#!/usr/bin/env python3

# TODO maybe instead use https://docs.aiohttp.org/en/stable/web_quickstart.html (and update readme)

from flask import Flask, request
from waitress import serve
import logging

log = logging.getLogger("status_listener")
log.setLevel(logging.DEBUG)

class StatusPostCallback:
    def receive_data(self, data):
        pass

def status_listen(callbacks, config):
    m_config = config["bridge_status"]["listen_endpoint"]
    app = Flask(__name__)

    @app.route('/', methods=['POST'])
    def request_post():
        for callback in callbacks:
            try:
                callback.receive_data(request.data)
            except:
                log.exception("Callback update failed")
        return 'ack'

    serve(app, host = m_config["host"], port = m_config["port"])
