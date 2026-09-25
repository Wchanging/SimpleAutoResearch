"""Frame process output without treating carriage-return updates as log lines."""

from typing import Callable


class ProcessMessage(str):
    """String-compatible callback message with transient display metadata."""

    def __new__(cls, stream: str, text: str, *, transient: bool = False):
        value = super().__new__(cls, f"{stream}: {text}")
        value.stream = stream
        value.transient = transient
        return value


class ProcessOutput:
    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback
        self.buffers = {"stdout": "", "stderr": ""}
        self.carriage = {"stdout": False, "stderr": False}

    def _emit(self, stream: str, transient: bool = False):
        text = self.buffers[stream]
        self.buffers[stream] = ""
        if text.strip():
            self.callback(ProcessMessage(stream, text, transient=transient))

    def feed(self, stream: str, chunk: str):
        # Each pipe has its own reader; stdout/stderr fragments must not mix.
        for char in chunk:
            if self.carriage[stream]:
                self.carriage[stream] = False
                if char == "\n":
                    self._emit(stream)
                    continue
                self._emit(stream, transient=True)
            if char == "\r":
                self.carriage[stream] = True
            elif char == "\n":
                self._emit(stream)
            else:
                self.buffers[stream] += char
                # Bound unterminated output without dropping ordinary log lines.
                if len(self.buffers[stream]) >= 8192:
                    self._emit(stream)

    def finish(self):
        for stream in self.buffers:
            self._emit(stream)
            self.carriage[stream] = False
