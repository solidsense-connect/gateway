
from time import time, sleep, localtime
from threading import Thread, Lock

import importlib, pathlib
import sys

from wirepas_gateway.json_plugin import JSONPlugin

# **********************************************


class PluginManager:
    """
    """

    def __init__(self, logger, sink_manager, mqtt_wrapper, settings, unknown_arguments):
        self.logger = logger

        self._plugins = []
        if settings.plugin_json:
            self._plugins.append(JSONPlugin(logger, sink_manager, mqtt_wrapper, settings))
        if settings.plugin_load:
            plugin_list = settings.plugin_load.split(",")
            for plugin in plugin_list:
                path = pathlib.PurePosixPath(plugin)
                sys.path.append(str(path.parent))
                module = importlib.import_module(path.stem)
                self._plugins.append(module.load(logger, sink_manager, mqtt_wrapper, settings, unknown_arguments))

        self.drop_protobuf = settings.drop_protobuf and bool(self._plugins)
        if self.drop_protobuf:
            self.logger.warning("Protobuf messages will never be sent to the broker")

    # *****************************************************************

    def start(self):
        for plugin in self._plugins:
            plugin.start()

    # *****************************************************************

    def on_connect_hook(self):
        for plugin in self._plugins:
            plugin.on_connect_hook()

    # *****************************************************************

    def on_data_received_hook(
            self,
            sink_id,
            timestamp,
            src,
            dst,
            src_ep,
            dst_ep,
            travel_time,
            qos,
            hop_count,
            data,
    ):
        drop = self.drop_protobuf

        if self._plugins:
            self.logger.debug("Hook node(%u) EP(%u) - APDU(%s)", src, dst_ep, str(data))

            for plugin in self._plugins:
                drop |= plugin.on_data_received_hook(sink_id, timestamp, src, dst, src_ep, dst_ep, travel_time, qos,
                                                     hop_count, data)

        return drop
