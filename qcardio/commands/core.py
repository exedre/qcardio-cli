from .base import PluginBase

class Plugin(PluginBase):
    def __init__(self, cfg, state):
        self.cfg   = cfg
        self.state = state
        
    def discover(self):
        return "CORE: discover not implemented"

    def read(self, uuid):
        return f"CORE: read {uuid}"

    def write(self, uuid, val):
        return f"CORE: write {uuid}={val}"

    def measure(self):
        return "CORE: measure not implemented"
