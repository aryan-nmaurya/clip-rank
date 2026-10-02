class RankedSourceRejected(ValueError):
    def __init__(self, reason, title=None):
        super().__init__(reason)
        self.title = title
