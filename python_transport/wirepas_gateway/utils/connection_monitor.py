import os
from threading import Lock


class ConnectionMonitorBase:
    def on_connecting(self):
        pass

    def on_connected(self):
        pass

    def on_exit(self):
        pass


class ConnectionMonitor(ConnectionMonitorBase):
    """
        Object dedicated to MQTT connection monitoring, in a simple
        and "Thread-safe" way.
    """

    def __init__(self, status_led=None, status_filename=None):
        self._lock = Lock()
        self._status_led = status_led
        self._status_filename = status_filename

    def _write_status_atomic(self, status):
        tmp_filename = self._status_filename + ".tmp"
        f = open(tmp_filename, 'w')
        closed = False
        try:
            f.write(status)
            f.flush()
            os.fsync(f.fileno())
            f.close()
            closed = True
            os.rename(tmp_filename, self._status_filename)

        finally:
            if not closed:
                f.close()

    def on_connecting(self):
        with self._lock:
            if self._status_led is not None:
                self._status_led.in_progress()
            if self._status_filename is not None:
                self._write_status_atomic("CONNECTING...")

    def on_connected(self):
        with self._lock:
            if self._status_led is not None:
                self._status_led.ok()
            if self._status_filename is not None:
                self._write_status_atomic("CONNECTED")

    def on_exit(self):
        with self._lock:
            if self._status_led is not None:
                self._status_led.off()
            if self._status_filename is not None:
                self._write_status_atomic("NOT RUNNING")
