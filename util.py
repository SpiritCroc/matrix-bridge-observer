import inspect
import logging
import os
from threading import Event, Thread


# Directory containing this file
this_dir = os.path.realpath(os.path.abspath(os.path.split(inspect.getfile( inspect.currentframe() ))[0]))

def relative_path(path):
    return "{0}/{1}".format(this_dir, path)

# Base class for looper thread classes
class Looper:
    def __init__(self, logname):
        self.event = Event()
        self.alive = True
        self.thread = None
        self.log = logging.getLogger(logname)
    def prepare_loop(self):
        pass
    def prepare_thread(self):
        pass
    def on_loop(self):
        pass
    def update_loop_wrapper(self, func):
        self.prepare_thread()
        while self.alive:
            self.prepare_loop()
            if not self.alive:
                break
            try:
                self.event.clear()
                time = func()
                self.on_loop()
                self.event.wait(time)
            except:
                self.log.exception("Broken loop")
                self.event.wait(1)
        self.log.info("Stopped")
    def start_loop(self, func):
        self.thread = Thread(target=self.update_loop_wrapper, args=(func,))
        self.thread.start()
    def update_now(self, updated_by):
        self.event.set()
    def stop(self):
        self.log.info("Initiating stop")
        self.alive = False
        self.update_now(self)
        try:
            self.thread.join()
        except:
            self.log.exception("stop")

async def get_bridges(client, room):
    if isinstance(room, str):
        room_id = room
    else:
        room_id = room.room_id
    result = await client.room_get_state(room_id = room_id)
    bridges = []
    for event in result.events:
        try:
            if event['type'] == "m.bridge" or event['type'] == "uk.half-shot.bridge":
                content = event["content"]
                #bridge_id = content["protocol"]["id"]
                #bridgebot
                bridges.append(content)
        except KeyError as e:
            continue
    return bridges
