
from time import time, sleep, localtime
from threading import Thread, Lock

import json
from wirepas_mesh_messaging import GatewayResultCode


PLUGIN_FIRMWARE_VERSION = "1.0.0"

# **********************************************

def deferred_thread(fn):
    """
    Decorator to handle a request on its own Thread
    to avoid blocking the calling Thread on I/O.
    It creates a new Thread but it shouldn't impact the performances
    as requests are not supposed to be really frequent (few per seconds)
    """

    def wrapper(*args, **kwargs):
        thread = Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread

    return wrapper


class JSONPlugin:
    """
    """

    def __init__(self, logger, sink_manager, mqtt_wrapper, settings):
        self.logger = logger
        self.sink_manager = sink_manager
        self.mqtt_wrapper = mqtt_wrapper

        self.event_id = 0

        # publish topics
        self.packet_response_topic = "wirepas-json-response/packet/" + settings.gateway_id
        self.packet_event_topic = "wirepas-json-event/packet/" + settings.gateway_id + "/{}/{}"
        # subscribe topics
        self.packet_request_topic = "wirepas-json-request/packet/" + settings.gateway_id

    # *****************************************************************

    def start(self):
        self.logger.info("JSON Plugin ready! (Version %s)", PLUGIN_FIRMWARE_VERSION)

    # *****************************************************************

    def _send_message(self, sink_id, destination_address, source_endpoint, destination_endpoint, data):
        result = None
        self.logger.debug("send message to 0x%x (EP %d) with sink(%s) - data: %s",
                          destination_address, destination_endpoint, sink_id if sink_id is not None else "all",
                          data.hex())

        if sink_id:
            sink = self.sink_manager.get_sink(sink_id)
            if sink:
                sinks = [sink]
            else:
                result = "Unknown sink {}".format(sink_id)
                sinks = []
        else:
            sinks = self.sink_manager.get_sinks()

        for sink in sinks:
            res = sink.send_data(destination_address, source_endpoint, destination_endpoint, 0, 0, data)
            if res != GatewayResultCode.GW_RES_OK:
                result = "Wirepas sink error {}".format(res)
                self.logger.error(result)

        return result

    @deferred_thread
    def _on_packet_request_received(self, client, userdata, message):
        self.logger.info("Wirepas packet request received on topic " + message.topic)
        self.logger.debug(message.payload)

        reply_string = "OK"
        ack_id = None
        try:
            request = json.loads(message.payload)

            # parse optional params
            ack_id = request.get('ack_id', None)
            ack_id = int(ack_id) if ack_id is not None else None
            sink_id = request.get('sink_id', None)

            # parse mandatory params
            destination_address = int(request['destination_address'])
            source_endpoint = int(request['source_endpoint'])
            destination_endpoint = int(request['destination_endpoint'])
            data = bytes.fromhex(request['data'])

            try:
                result = self._send_message(sink_id, destination_address, source_endpoint, destination_endpoint,
                                            data)
                if result:
                    reply_string = result

            except Exception:
                self.logger.exception("Impossible to send wirepas message")
                reply_string = "Impossible to send wirepas message"

        except Exception as e:
            self.logger.exception("Impossible to process json command")
            reply_string = e.__str__()

        if ack_id is not None:
            message = {'ack_id': ack_id, 'status': reply_string}
            message_json = json.dumps(message)
            self.logger.debug(message_json)
            self.mqtt_wrapper.publish(self.packet_response_topic, message_json)

    # *****************************************************************

    def on_connect_hook(self):
        self.mqtt_wrapper.subscribe(self.packet_request_topic, self._on_packet_request_received)

    # *****************************************************************

    def _get_event_id(self):
        self.event_id = (self.event_id + 1) % 0xFFFF
        return self.event_id

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
        # Wirepas packet translation to JSON
        message = {
            'sink_id': sink_id,
            'event_id': self._get_event_id(),
            'source_address': src,
            'source_endpoint': src_ep,
            'destination_endpoint': dst_ep,
            'tx_time_ms_epoch': timestamp - travel_time,
            'data': data.hex()
        }

        message_json = json.dumps(message)
        self.logger.debug(message_json)
        self.mqtt_wrapper.publish(self.packet_event_topic.format(src, dst_ep), message_json)

        return False
