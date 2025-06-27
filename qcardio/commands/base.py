class PluginBase:
    def __init__(self, cfg, state):
        self.cfg   = cfg
        self.state = state

    def discover(self):
        raise NotImplementedError

    def read(self, uuid):
        raise NotImplementedError

    def write(self, uuid, val):
        raise NotImplementedError
