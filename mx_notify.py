from mx_base import BaseMatrixUser
from bridge_info import BridgeStatusUpdateCallback
from user_watch import WatchedUserUpdateCallback


class MatrixNotify(BaseMatrixUser, BridgeStatusUpdateCallback, WatchedUserUpdateCallback):
    def __init__(self, config):
        m_config = config["notify"]["matrix"]
        super().__init__("MatrixNotify", m_config)

        self.room_id = m_config["room_id"]

    def bridge_update(self, bridge, user_label, user_id, state, previous_state, is_good, is_bad, bad_since, alert):
        if previous_state != state or alert:
            color = self.get_color(is_good, is_bad)
            msg = f"[{state}] {bridge} {user_label}"
            formatted_msg = f'<font color="{color}">[{state}]</font> {bridge} {user_label}'
            # Bigger message, unless this is the first state that we heard since script start
            #if previous_state != None or alert:
            #    formatted_msg = f"<h4>{formatted_msg}</h4>"
            async def update_fun(client):
                content = {
                    "msgtype": "m.text" if alert else "m.notice",
                    "format": "org.matrix.custom.html",
                    "body": msg,
                    "formatted_body": formatted_msg
                }
                await client.room_send(self.room_id, "m.room.message", content, ignore_unverified_devices=True)
            self.with_client(update_fun)

    def get_color(self, is_good, is_bad):
        # Colors for good / not good same as sent from ruby-grafana
        return "#10a345" if is_good else (
            "#ed2e18" if is_bad else "#f79520"
        )

    def watched_user_update(self, user, bridge, is_good, alert, info):
        msg = "[OK]" if is_good else "[ALERT]"
        color = self.get_color(is_good == True, is_good == False)
        formatted_msg = f'<font color="{color}">{msg}</font>'
        msg_add = ""
        if bridge == None:
            # Bridge-independent warnings
            msg_add = f"{user}: "
            if info != None:
                msg_add += info
            else:
                # No use case for this one?
                msg_add += "unexpected update"
        else:
            msg_add = f"{bridge} {user}"
            if info != None:
                msg_add += ": " + info
        msg += " " + msg_add
        formatted_msg += " " + msg_add
        async def update_fun(client):
            content = {
                "msgtype": "m.text" if alert else "m.notice",
                "format": "org.matrix.custom.html",
                "body": msg,
                "formatted_body": formatted_msg
            }
            await client.room_send(self.room_id, "m.room.message", content, ignore_unverified_devices=True)
        self.with_client(update_fun)
