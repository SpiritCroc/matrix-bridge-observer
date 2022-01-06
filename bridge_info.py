#!/usr/bin/env python3

from datetime import datetime, timedelta
import json
import logging
from urllib import request, parse

from status_listener import StatusPostCallback
from util import Looper

GOOD_STATES = ["CONNECTED", "BACKFILLING"]
OK_STATES = ["TRANSIENT_DISCONNECT", "CONNECTING"] + GOOD_STATES

class BridgeStatusUpdateCallback:
    def bridge_update(self, bridge, user_label, user_id, state, previous_state, is_good, is_bad, bad_since, alert):
        pass

class BridgeWatcher(Looper, StatusPostCallback):
    def __init__(self, bridge_id, config, user_label, user_id, remote_id, bridgeWatcher):
        super().__init__(f"BridgeWatcher_{bridge_id}_{user_label}")
        self.bridge_id = bridge_id
        self.bridgeWatcher = bridgeWatcher
        # Whether we can identify incoming status_endpoint data as (ir-)relevant.
        # We can only do this once we did a status request and populated more data.
        self.identifiable = remote_id != None
        self.user_id = user_id
        self.user_label = user_label
        self.remote_id = remote_id
        self.remote_name = None
        self.state = None
        self.reported_state = None
        if "bridge_url" in config:
            self.bridge_url = config["bridge_url"] + "/_matrix/app/com.beeper.bridge_state"
        else:
            self.log.warning("bridge_url not set - bridge crashes will not be detected")
            self.bridge_url = None
        self.hs_token = config["hs_token"]
        self.max_ttl = config["max_ttl"]
        self.ttl_if_unreachable = config["ttl_if_unreachable"]
        self.alert_delay = timedelta(seconds=config["alert_delay"])
        self.alert_period = timedelta(seconds=config["alert_period"])
        self.pending_data = None
        self.bad_since = datetime.now()
        self.last_alert = None
        self.start_loop(self.update_loop)
    def accepts_data(self, data):
        if not self.identifiable:
            return False
        try:
            parsed = json.loads(data)
            # Check if data relevant
            if (self.user_id == parsed["user_id"] and
                    self.remote_id != None and self.remote_id == parsed["remote_id"]):
                    #self.remote_name != None and self.remote_name == parsed["remote_name"]):
                return True
            return False
        except Exception as e:
            self.log.info(f"Not accepting data with exception {e}")
            return False
    def receive_data(self, data): # called from external updates
        parsed = json.loads(data)
        if self.accepts_data(data):
            # Schedule an update
            self.pending_data = parsed
            self.update_now(self)
    def _handle_status(self, parsed):
        try:
            # Check remote_id
            if self.remote_id == None:
                self.remote_id = parsed["remote_id"]
            elif self.remote_id != parsed["remote_id"]:
                self.log.warning("remote_id mismatch ({self.remote_id}, {parsed['remote_id']})! did the user login a different account?")
                self.remote_id = parsed["remote_id"]

            # Check remote_name
            if self.remote_name == None:
                self.remote_name = parsed["remote_name"]
            elif self.remote_name != parsed["remote_name"]:
                self.log.warning("remote_name mismatch ({self.remote_name}, {parsed['remote_name']})! did the user login a different account?")
                self.remote_name = parsed["remote_name"]

            self.state = parsed["state_event"]
            next_alert_ttl = self.update_callbacks()

            return min(self.max_ttl, parsed["ttl"], next_alert_ttl)
        except Exception as e:
            self.log.error(f"Error parsing {parsed}")
            raise e

    def update_callbacks(self):
        is_good = self.state in GOOD_STATES
        is_bad = self.state not in OK_STATES
        alert = False
        next_alert_ttl = self.max_ttl
        if is_bad:
            now = datetime.now()
            if self.bad_since == None:
                self.bad_since = now
            if now >= self.bad_since + self.alert_delay:
                if self.last_alert == None or now >= self.last_alert + self.alert_period:
                    alert = True
                    next_alert_ttl = self.alert_period.seconds
                else:
                    # Add 1 to ensure seconds are kind of rounded up (to avoid too frequent checks)
                    next_alert_ttl = ((self.last_alert + self.alert_period) - now).seconds + 1
            else:
                # Add 1 to ensure seconds are kind of rounded up (to avoid too frequent checks)
                next_alert_ttl = ((self.bad_since + self.alert_delay) - now).seconds + 1
        else:
            self.bad_since = None
            self.last_alert = None
        if alert:
            self.last_alert = now
        for callback in self.bridgeWatcher.callbacks:
            try:
                callback.bridge_update(self.bridge_id, self.user_label, self.user_id, self.state, self.reported_state, is_good, is_bad, self.bad_since, alert)
            except:
                self.log.exception("Exception trying to update callback")
        self.reported_state = self.state
        return next_alert_ttl

    def fetch_status(self):
        headers = {
            "Authorization": f"Bearer {self.hs_token}"
        }
        args = parse.urlencode({
            "user_id": self.user_id
        })
        data = parse.urlencode({}).encode()
        req = request.Request(f"{self.bridge_url}?{args}", data=data, headers=headers)
        resp = request.urlopen(req).read().decode()
        parsed = json.loads(resp)
        remoteState = parsed["remoteState"]
        if self.remote_id != None:
            return self._handle_status(remoteState[self.remote_id])
        elif len(remoteState) > 1:
            raise Exception("Multiple remote states without specified remote_id in your config, not supported yet!")
        for remote_id in remoteState:
            return self._handle_status(remoteState[remote_id])

    def update_loop(self):
        try:
            if self.pending_data != None:
                result = self._handle_status(self.pending_data)
                self.pending_data = None
                self.identifiable = True
                return result
            elif self.bridge_url != None:
                result = self.fetch_status()
                self.identifiable = True
                return result
            else:
                # Not supported
                return self.max_ttl
        except:
            self.log.exception("Reading status failed")
            self.state = "UNREACHABLE"
            next_alert_ttl = self.update_callbacks()
            return min(self.max_ttl, next_alert_ttl, self.ttl_if_unreachable)


class BridgesWatcher:
    def __init__(self, config, callbacks):
        self.log = logging.getLogger("BridgesWatcher")
        self.bridge_watchers = []
        self.callbacks = callbacks

        m_config = config["bridge_status"]
        bridges = m_config["bridges"]

        for bridge_id in bridges:
            bridge = bridges[bridge_id]
            for user_label in bridge["users"]:
                user = bridge["users"][user_label]
                user_id = user["user_id"]
                remote_id = user["remote_id"] if ("remote_id" in user) else None
                self.bridge_watchers.append(BridgeWatcher(bridge_id, bridge, user_label, user_id, remote_id, self))
    def receive_data(self, data):
        accepters = []
        for watcher in self.bridge_watchers:
            if watcher.accepts_data(data):
                accepters.append(watcher)
        if len(accepters) > 1:
            self.log.error(f"Discarding ambiguous update {data}")
            # Refresh all possibly relevant as fallback
            for watcher in accepters:
                watcher.update_now(self)
        elif len(accepters) == 1:
            accepters[0].receive_data(data)
