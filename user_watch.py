from datetime import datetime, timedelta
from mx_base import BaseMatrixUser
from nio.responses import SyncError, SyncResponse
from nio import RoomMessage, MegolmEvent, RoomMessagesResponse, MessageDirection
from util import Looper, get_bridges

SYNC_TIMEOUT_MILLIS = 30_000
SYNC_DELAY_SECONDS = 5

class WatchedUserUpdateCallback:
    def watched_user_update(self, user, bridge, is_good, alert, info):
        pass

class UserBridgeState:
    def __init__(self, bridge_id, config, log):
        self.log = log
        self.bridge_id = bridge_id
        self.alert_after_inactivity = timedelta(seconds = config["alert_after_inactivity"])
        self.alert_period = timedelta(seconds = config["alert_period"])
        self.last_update_was_good = True
        self.last_alert_ts = None
        self.last_not_good_notify_ts = None
        self.posted_any_update = False
        self.last_bridged_message_ts = 0
        if "explicit_rooms" in config:
            self.explicit_rooms = config["explicit_rooms"]
        else:
            self.explicit_rooms = []
        if "require_sender" in config:
            self.require_sender = config["require_sender"]
        else:
            self.require_sender = None
        self.send_on_inactivity = "send_on_inactivity" in config
        if self.send_on_inactivity:
            inactivity_config = config["send_on_inactivity"]
            self.inactivity_delay = timedelta(seconds = inactivity_config["delay_alert"])
            self.last_inactivity_send_ts = None
            self.inactivity_send_user = inactivity_config["send_user"]
            self.inactivity_send_room = inactivity_config["send_room"]
            self.inactivity_send_text = inactivity_config["send_text"]
    def update_latest_event(self, event, watched_user):
        if event.sender == watched_user.mx_id:
            # Own events aren't bridged events (or if they are, we can't tell),
            # and thus don't tell if the bridge is working
            return False
        if self.require_sender != None and self.require_sender != event.sender:
            return False
        if isinstance(event, RoomMessage) or isinstance(event, MegolmEvent):
            self.last_bridged_message_ts = max(event.server_timestamp, self.last_bridged_message_ts)
            return True
        return False
    def oldest_interesting_timestamp(self, now):
        return int((now - self.alert_after_inactivity).timestamp()*1000)
    async def maybe_update_callback(self, now, watched_user, client):
        last_bridged_message_ts = datetime.fromtimestamp(self.last_bridged_message_ts/1000)
        alert =  now - last_bridged_message_ts > self.alert_after_inactivity
        post_update = False
        info = None

        if not self.posted_any_update:
            post_update = True

        if not self.last_update_was_good and not alert:
            post_update = True
        elif alert and self.last_alert_ts == None:
            post_update = True
        elif alert and now - self.last_alert_ts > self.alert_period:
            post_update = True

        if not post_update:
            return

        is_good = not alert
        # Maybe delay alert, if we have send_on_inactivity set up
        if alert and self.send_on_inactivity:
            # Did try before to do an inactivity send?
            if (self.last_inactivity_send_ts == None or
                        # Last inactivity send was for an older alert?
                        self.last_not_good_notify_ts == None or
                        self.last_inactivity_send_ts < self.last_not_good_notify_ts
                    ):
                self.last_inactivity_send_ts = now
                try:
                    self.log.debug(f"Send activity as user {self.inactivity_send_user}")
                    await watched_user.userWatcher.send_with_user(watched_user, client, self.inactivity_send_user, self.inactivity_send_room, self.inactivity_send_text)
                    self.log.debug(f"Sent activity as user {self.inactivity_send_user}")
                except:
                    self.log.exception("Inactivity send failed")
                # Still post update, but do not alert just yet
                alert = False
            elif now - self.last_inactivity_send_ts <= self.inactivity_delay:
                if self.posted_any_update:
                    # Skip alert
                    return
                else:
                    alert = False

        self.posted_any_update = True
        if self.last_bridged_message_ts == 0:
            info = f"No recent message found"
        else:
            info = f"Last received message: {last_bridged_message_ts}"
        if alert:
            self.last_alert_ts = now
        else:
            self.last_alert_ts = None
        if is_good:
            self.last_not_good_notify_ts = None
        else:
            self.last_not_good_notify_ts = now
        self.last_update_was_good = is_good
        self.log.debug(f"Update callbacks: {self.bridge_id}, is_good={is_good}, alert={alert}, info={info}")
        watched_user.update_callbacks(self.bridge_id, is_good, alert, info)

class WatchedUser(BaseMatrixUser, Looper):
    def __init__(self, user_id, config, userWatcher):
        logname = f"UserWatch_{user_id}"
        BaseMatrixUser.__init__(self, logname, config)
        Looper.__init__(self, logname)
        self.user_id = user_id
        self.userWatcher = userWatcher
        if "watched_bridge_ids" in config:
            wbc = config["watched_bridge_ids"]
        else:
            # Empty fallback, in case this user is not watched, but only used for sending
            wbc = dict()
        if "auto_mark_read_rooms" in config:
            self.auto_mark_read_rooms = config["auto_mark_read_rooms"]
        else:
            self.auto_mark_read_rooms = []
        if "auto_mark_read_rooms_without_notification" in config:
            self.auto_mark_read_rooms_without_notification = config["auto_mark_read_rooms_without_notification"]
        else:
            self.auto_mark_read_rooms_without_notification = []
        self.watched_bridge_ids = wbc.keys()
        self.bridge_states = dict()
        for x in wbc:
            self.bridge_states[x] = UserBridgeState(x, wbc[x], self.log)
        self.sync_next_batch_token = None
        self.start_loop(self.update_loop)

    def update_loop(self):
        try:
            result = self.with_client(self.check_rooms)
            #return result if result != None else 120 # TODO? config: timeout in case of error
        except:
            self.log.exception("Error in update loop")
            self.update_callbacks(None, False, True, "Internal error")
            #return 120 # TODO? config: timeout in case of error
        return SYNC_DELAY_SECONDS

    async def check_rooms(self, client):
        # TODO? based on alert_after_inactivity, alert_period, and last activity (or fallback config?)
        ttl_to_next_timeout = SYNC_DELAY_SECONDS
        if self.sync_next_batch_token != None:
            self.log.debug("Incremental sync")
            client.next_batch = self.sync_next_batch_token
        else:
            self.log.debug("Initial sync")
        #rooms = client.rooms.values()
        sync_response = await client.sync(SYNC_TIMEOUT_MILLIS)
        if isinstance(sync_response, SyncError):
            self.update_callbacks(None, False, True, info = f"Sync failed: {sync_response.message}")
            return None # error case
        elif not isinstance(sync_response, SyncResponse):
            self.update_callbacks(None, False, True, info = f"Sync failed: {sync_response}")
            return None # error case
        joins = sync_response.rooms.join
        self.log.debug(f"Handle {len(joins)} rooms")
        for room_id in joins:
            # Wants mark as read: room is in list, and has appropriate notification counts
            # Needs mark as read: wants mark as read, but was not able to update read marker yet due to missing timeline events
            wants_mark_as_read = False
            needs_mark_as_read = False
            # Check if room should be marked as unread
            if room_id in self.auto_mark_read_rooms:
                wants_mark_as_read = True
            elif room_id in self.auto_mark_read_rooms_without_notification:
                notifs = joins[room_id].unread_notifications
                if notifs.notification_count == 0 and notifs.highlight_count == 0:
                    wants_mark_as_read = True
            # Mark room as unread if possible
            if wants_mark_as_read:
                if len(joins[room_id].timeline.events) == 0:
                    needs_mark_as_read = True
                else:
                    try:
                        latest_event = joins[room_id].timeline.events[-1].event_id
                        self.log.debug(f"Update read marker for {room_id} to {latest_event}")
                        await client.room_read_markers(room_id, latest_event, latest_event)
                    except:
                        self.log.exception("Failed to update read marker for {room_id}")
            relevant_bridge_states = []
            # Check explicit rooms (overwrite bridges):
            for bridge_state in self.bridge_states.values():
                if room_id in bridge_state.explicit_rooms:
                    relevant_bridge_states.append(bridge_state)

            if len(relevant_bridge_states) == 0:
                bridges = await get_bridges(client, room_id)
                bridge_ids = []
                for bridge in bridges:
                    try:
                        bridge_id = bridge["protocol"]["id"]
                        if bridge_id not in bridge_ids:
                            bridge_ids.append(bridge_id)
                    except:
                        pass
                if len(bridge_ids) != 1:
                    if len(bridge_ids) > 0:
                        self.log.debug(f"Skip {room_id}: no unique bridge: {bridge_ids}")
                    continue
                bridge = bridge_ids[0]
                if bridge not in self.watched_bridge_ids:
                    # Not a watched bridge
                    continue
                bridge_state = self.bridge_states[bridge]
                if bridge_state not in relevant_bridge_states:
                    relevant_bridge_states.append(bridge_state)

            for bridge_state in relevant_bridge_states:
                found_event = self.handle_events(bridge_state, reversed(joins[room_id].timeline.events))

                # If not found: use messages API https://spec.matrix.org/v1.1/client-server-api/#get_matrixclientv3roomsroomidmessages
                room_token = joins[room_id].timeline.prev_batch
                while not found_event:
                    # TODO filter using message_filter?
                    self.log.debug(f"Backfill {room_id} ({bridge_state.bridge_id}) - {room_token}")
                    room_response = await client.room_messages(room_id, start = room_token, direction = MessageDirection.back)
                    if isinstance(room_response, RoomMessagesResponse):
                        if needs_mark_as_read:
                            needs_mark_as_read = False
                            try:
                                latest_event = room_response.chunk[0].event_id
                                self.log.debug(f"Update read marker for {room_id} to {latest_event}")
                                await client.room_read_markers(room_id, latest_event, latest_event)
                            except:
                                self.log.exception("Failed to update read marker for {room_id}")
                        # TODO does this make sense?
                        if room_response.end == room_token:
                            # Do not re-run
                            found_event = True
                        room_token = room_response.end
                        if self.handle_events(bridge_state, room_response.chunk):
                            found_event = True
                    else:
                        # abort
                        found_event = True
                self.log.debug(f"Done checking {room_id} ({bridge_state.bridge_id})")
        self.log.debug(f"Done handling {len(joins)} rooms")

        now = datetime.now()
        for bridge_state in self.bridge_states.values():
            # TODO calculate ttls in there?
            await bridge_state.maybe_update_callback(now, self, client)
        self.sync_next_batch_token = sync_response.next_batch
        return ttl_to_next_timeout

    def handle_events(self, bridge_state, events):
        found_event = False
        now = datetime.now()
        ts_limit = bridge_state.oldest_interesting_timestamp(now)
        for event in events:
            if bridge_state.update_latest_event(event, self):
                found_event = True
                break
            if event.server_timestamp < ts_limit:
                # We do not want to go infinitely in the past if it doesn't matter either way
                found_event = True
                break
            if event.server_timestamp < bridge_state.last_bridged_message_ts:
                # No need to search further, we already know a newer event
                found_event = True
                break
        return found_event

    def update_callbacks(self, bridge_id, is_good, alert, info = None):
        for callback in self.userWatcher.callbacks:
            try:
                callback.watched_user_update(self.mx_id, bridge_id, is_good, alert, info)
            except:
                self.log.exception("Exception trying to update callback")



class UserWatcher:
    def __init__(self, config, callbacks):
        self.users = []
        self.callbacks = callbacks

        m_config = config["watched_users"]
        for user_id in m_config:
            user = m_config[user_id]
            self.users.append(WatchedUser(user_id, user, self))

    async def send_with_user(self, watched_user, watched_user_client, user_id, room_id, text):
        for user in self.users:
            if user.user_id == user_id:
                async def fun(client):
                    content = {
                        "msgtype": "m.text",
                        "body": text
                    }
                    await client.room_send(room_id, "m.room.message", content, ignore_unverified_devices=True)
                if watched_user == user:
                    # Use client directly, or it would block, since we already have a client
                    await fun(watched_user_client)
                else:
                    user.with_client(fun)
                return
        raise Exception(f"User {user_id} not found in watched users")
