#!/usr/bin/env python3

import logging
from logging import Formatter, StreamHandler
from logging.handlers import TimedRotatingFileHandler
import sys
import yaml

from bridge_info import BridgesWatcher
from user_watch import UserWatcher
from mx_notify import MatrixNotify
from status_listener import StatusPostCallback, status_listen
from util import relative_path as rp
import nest_asyncio

mainlog = logging.getLogger("")
mainlog.setLevel(logging.DEBUG)
log = logging.getLogger("main")
log.setLevel(logging.DEBUG)

with open(rp('config.yaml')) as fin:
    config = yaml.full_load(fin)

class StatusPrinter(StatusPostCallback):
    def receive_data(self, data):
        log.info(f"Received data: {data}")
    def bridge_update(self, bridge, user_label, user_id, state, previous_state, is_good, is_bad, bad_since, alert):
        log_msg = f"Received bridge update: {bridge}, {user_label}, {user_id}, {state}, alert={alert}"
        if state == previous_state:
            log.debug(log_msg)
        else:
            log.info(log_msg)

if __name__ == "__main__":
    # Allow one matrix nio client invoke a callback for another one, both using their own asyncio loops
    nest_asyncio.apply()
    # TODO copy log config stuff as done in mautrix-*
    logrotate = TimedRotatingFileHandler(
            "/daten/links/matrix-bridges/bridge-observer/log", #get_path(config.get("logging", "file")),
            when="d", #config.get("logging", "when"),
            interval=1, #config.getint("logging", "interval"),
            backupCount=7 #config.getint("logging", "backupCount")
    )
    logrotate.setFormatter(Formatter(
            "%(asctime)s: %(levelname)s: %(name)s: %(message)s" #config.get("logging", "format", raw=True)
    ))
    logrotate.setLevel(logging.DEBUG)
    #mainlog.addHandler(logrotate)
    stdouthandler = StreamHandler(sys.stdout)
    stdouthandler.setFormatter(Formatter(
            "%(asctime)s: %(levelname)s: %(name)s: %(message)s" #config.get("logging", "stdout_format", raw=True)
    ))
    stdouthandler.setLevel(logging.DEBUG)
    if sys.stdout.isatty() or True:
        stdouthandler.setLevel(logging.DEBUG)
    else:
        # Be less verbose for systemd log
        stdouthandler.setLevel(logging.WARNING)
    mainlog.addHandler(stdouthandler)

    mx_notifier = MatrixNotify(config)
    status_printer = StatusPrinter()

    bridge_callbacks = [
        status_printer,
        mx_notifier
    ]
    log.info("Starting bridge watchers...")
    bridgesWatcher = BridgesWatcher(config, bridge_callbacks)

    user_callbacks = [
        mx_notifier
    ]
    log.info("Starting user watchers...")
    userWatcher = UserWatcher(config, user_callbacks)

    listen_callbacks = [
        StatusPrinter(),
        bridgesWatcher
    ]
    log.info("Starting status listener...")
    status_listen(listen_callbacks, config)
