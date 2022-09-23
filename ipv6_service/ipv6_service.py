#!/usr/bin/python3

import fcntl
import os
import struct
import subprocess
import socket
import select
import logging
import time
import argparse

from threading import Thread

from wirepas_gateway.dbus.dbus_client import BusClient
from wirepas_tlv_app_config import WirepasTLVAppConfig

WIREPAS_IPV6_EP = 66

class Ipv6Add:
    """
    Class to handle ipv6 address and its associated wirepas fields
    An ipv6 address has following format:
        - 64 bits network prefix
        - 32 bits for sink address
        - 32 bits for node address 
    """
    def __init__(self, add_bytes, prefix_len=128):
        if add_bytes.__len__() < (prefix_len / 8):
            raise ValueError("Not a valid IPV6 address")
        self._add = add_bytes
        self._prefix_len = prefix_len

    @property
    def wirepas_node_add(self):
        # wirepas node address is the last 32 bits
        if self._prefix_len != 128:
            raise ValueError("Prefix len is not 128 to determine node address")

        return struct.unpack(">I", self._add[12:16])[0]

    @property
    def wirepas_sink_add(self):
        # wirepas sink address is the first 32 bits of the interface address
        if self._prefix_len < 96:
            raise ValueError("Prefix len is smaller than 96 to determine sink address")

        return struct.unpack(">I", self._add[8:12])[0]

    @property
    def prefix(self):
        if self._prefix_len & 7 != 0:
            raise ValueError("Prefix len is not a multiple of 8")

        return self._add[0:self._prefix_len>>3]


    @property
    def add(self):
        return self._add

    @property
    def prefix_len(self):
        return self._prefix_len

    def start_with_prefix(self, prefix_bytes):
        for i in range(prefix_bytes.__len__()):
            if prefix_bytes[i] != self._add[i]:
                return False
        return True

    @classmethod
    def from_srting(cls, add_str):
        # Holder for the address
        add_bytes = bytearray()

        def _append_groups(grps):
            for g in grps:
                # Make it full 4 digits
                if g == '':
                    add_bytes.extend(b'\00\00')
                else:
                    full_g = '{:04x}'.format(int(g, base=16))
                    add_bytes.extend(bytearray.fromhex(full_g))

        
        # Extract prefix length and address
        fields = add_str.split("/", 1)
        add_str = fields[0]
        if len(fields) == 2:
            prefix_len = int(fields[1])
        else:
            prefix_len = 128

        # Split high part and lower part
        part1, part2 = add_str.split("::")
        groups1 = part1.split(":")
        groups2 = part2.split(":")

        _append_groups(groups1)

        zero_group_count = 8 - groups1.__len__() - groups2.__len__()
        for i in range(zero_group_count):
            add_bytes.extend(b'\00\00')

        _append_groups(groups2)

        return cls(add_bytes, prefix_len)

    @classmethod
    def from_prefix_and_sink_add(cls, prefix, sink_add):
        if prefix.prefix_len != 64:
            raise RuntimeError("Prefix is not 64 %s" % prefix)

        # Initialize address with prefix
        add_bytes = bytearray(prefix.add)

        # Modify it with sink address
        add_bytes[8:12] = sink_add.to_bytes(4, byteorder='big')

        prefix_len = prefix.prefix_len + 32

        return cls(add_bytes, prefix_len)

    @classmethod
    def from_prefix_sink_add_and_sink_node(cls, prefix, sink_add, node_add):
        if prefix.prefix_len != 64:
            raise RuntimeError("Prefix is not 64 %s" % prefix)

        # Initialize address with prefix
        add_bytes = bytearray(prefix.add)

        # Modify it with sink address
        add_bytes[8:12] = sink_add.to_bytes(4, byteorder='big')
        # Add node address
        add_bytes[12:16] = node_add.to_bytes(4, byteorder='big')

        return cls(add_bytes, 128)

    def __str__(self):
        try:
            if self._prefix_len < 128:
                return "%s/%s" % (self._add.hex(":", 2), self._prefix_len)
            else:
                return self._add.hex(":", 2)
        except TypeError:
            return self._add.hex()


class IPV6NetworkConfig():

    VERSION = 0
    """Class to represent network ipv6 config
       Not all flags supported yet"""
    def __init__(self, nonce=0, nw_prefix=None, off_mesh_service=None):
        self.nonce = nonce
        self.nw_prefix = nw_prefix
        self.off_mesh_service = off_mesh_service

    @classmethod
    def from_bytes(cls, bytes):
        version = (bytes[0] & 0xf0) >> 4
        nonce = bytes[0] & 0xf
        nw_prefix = None
        off_mesh_service = None

        config_size = len(bytes)

        if version != IPV6NetworkConfig.VERSION:
            logging.error("Unknown version for IPV6 Network config")
            raise ValueError("Unknown version for IPV6 config")

        index = 1
        # Iterate on entries
        while index < config_size:
            entry = bytes[index]
            s = (entry & 0x80) >> 7
            # No need to parse more for now

            if s == 0:
                # It is a context and at least 9 bytes are required
                if (index + 9) > config_size:
                    logging.error("IPV6 config, entry too small for prefix")
                    raise ValueError("entry too small for prefix")

                if nw_prefix != None:
                    logging.error("Multiple prefix defined, not supported yet")
                else:
                    # It is a context (no need to check more) of 8 bytes
                    nw_prefix = Ipv6Add(bytes[index + 1:index + 1 + 8 ], prefix_len=64)

                index += 9
            else:
                # It is a target address and at least 17 bytes are required (No SID yet)
                if (index + 17) > config_size:
                    logging.error("IPV6 config, entry too small for off-mesh service")
                    raise ValueError("entry too small for for off-mesh service")

                if off_mesh_service != None:
                    logging.error("Multiple off-mesh addresses, not supported yet")
                else:
                    # SID shouldn't be set for now
                    off_mesh_service = Ipv6Add(bytes[index + 1:index + 1 + 16 ])

                index += 17

        return IPV6NetworkConfig(nonce, nw_prefix, off_mesh_service)


    def to_bytes(self):
        config = bytearray()
        # Create header
        header = (self.VERSION << 4) + (self.nonce)
        config.append(header)

        # Add prefix first if set
        if self.nw_prefix is not None:
            # S = 0, C = 0, CC = 0
            config.append(0)
            config.extend(self.nw_prefix.prefix)

        # Add off-mesh service if set
        if self.off_mesh_service is not None:
            # S = 1, C = 0, CC = 0
            config.append(0x80)
            config.extend(self.off_mesh_service.add)
        
        return config

    def increment_nonce(self):
        self.nonce = (self.nonce + 1) % 16
        return self


class IPV6Sink(Thread):

    UDP_INTERFACE_PORT = 6666

    APP_CONFIG_TLV_TYPE_PREFIX = 66

    """Class to represent a sink at ipv6 level"""
    def __init__(self, sink, nw_prefix, ext_interface_name, off_mesh_service = None):

        Thread.__init__(self)
        # Daemonize thread to exit with full process
        self.daemon = True
        self.running = False

        self._sink = sink

        self.nw_prefix = nw_prefix

        self._ext_interface = ext_interface_name

        self.off_mesh_service = off_mesh_service

        sink_config = self._sink.read_config()
        if not sink_config["started"]:
            # Do not add sink that are not started, will be done later
            logging.warning("Sink not started, do not add it yet")
            raise RuntimeError("Stack not started yet")

        # Create a set to store already added neighbor proxy entry
        # to avoid too many call to "ip"
        self._neigh_proxy = set()

        self._wp_address = sink_config["node_address"]

        # Network address not needed at the moment
        #self.network_add = sink.get_network_address()

        self._update_ipv6_config_to_app_config(sink_config)

        self._ipv6_interface_address = Ipv6Add.from_prefix_sink_add_and_sink_node(self.nw_prefix, self.wp_address, 0)

        self._ipv6_sink_prefix = Ipv6Add.from_prefix_and_sink_add(self.nw_prefix, self.wp_address)

        # Add broadcast address to list of neighbor proxy
        self.add_ndp_entry(0xffffffff)

        # Create socket pair to wakeup the thread waiting on data
        self._sp_w, self._sp_r = socket.socketpair()


    @property
    def wp_address(self):
        return self._wp_address

    @property
    def ipv6_interface_address(self):
        return self._ipv6_interface_address

    @property
    def ipv6_sink_prefix(self):
        return self._ipv6_sink_prefix

    def send_data(self, node_address, data):
        self._sink.send_data(
                    node_address,
                    WIREPAS_IPV6_EP,
                    WIREPAS_IPV6_EP,
                    1,
                    0,
                    data,
                    False,
                    0)

    def run(self):
        # Open UDP socket
        sock = socket.socket(socket.AF_INET6,
                        socket.SOCK_DGRAM)

        # Set socket to non blocking as we are using select
        sock.setblocking(0)

        # Bind it to ourself
        sock.bind(("%s" % self._ipv6_interface_address, self.UDP_INTERFACE_PORT))

        logging.debug("Waiting for packet")
        self.running = True
        while self.running:
            try:
                r, _, _ = select.select([sock, self._sp_r], [], [])
            except socket.error:
                continue

            if self._sp_r in r:
                # We were wakeuped from other thread, iterate again to check running state
                _ = self._sp_r.recv(1)
                continue

            if sock in r:
                try:
                    data, addr = sock.recvfrom(2048)
                    self.add_ndp_entry(Ipv6Add.from_srting(addr[0]).wirepas_node_add)
                    logging.debug(data)
                except socket.error:
                    logging.error("Cannot read socket even if select said that it was ready")

        sock.close()
        logging.info("Thread exited")


    def stop(self):
        """
        Stoping the thread witing on own socket
        """
        self.running = False

        logging.info("Stopping Thread")

        # Waking up the thread w
        self._sp_w.send(b"x")

        self.join()

        # make it a list to avoid size change while iteration
        for neigh in list(self._neigh_proxy):
            self.remove_ndp_entry(neigh)

    def add_ndp_entry(self, node_address):
        if node_address in self._neigh_proxy:
            # Already in neighbor proxy cache
            return

        add = Ipv6Add.from_prefix_sink_add_and_sink_node(self.nw_prefix, self.wp_address, node_address)
        IPV6Transport._execute_cmd("sudo ip neigh add nud permanent proxy %s dev %s extern_learn" % (add, self._ext_interface),
                                    True)

        self._neigh_proxy.add(node_address)


    def remove_ndp_entry(self, node_address):
        if node_address not in self._neigh_proxy:
            logging.error("Cannot remove proxy neighbor that was not previously added %x" % node_address)
            return

        add = Ipv6Add.from_prefix_sink_add_and_sink_node(self.nw_prefix, self.wp_address, node_address)
        IPV6Transport._execute_cmd("sudo ip neigh del proxy %s dev %s" % (add, self._ext_interface),
                                    True)

        self._neigh_proxy.discard(node_address)


    def _update_ipv6_config_to_app_config(self, sink_config=None):
        if sink_config is None:
            sink_config = self._sink.read_config()

        new_config = None
        # Add network prefix to app_config tlv with id 66
        try:
            current_app_config = sink_config["app_config_data"]
            app_config = WirepasTLVAppConfig.from_value(current_app_config)
        except KeyError:
            app_config = WirepasTLVAppConfig()
        except ValueError:
            #  Not tlv format, errase it
            logging.info("Current app config is not with TLV format, erase it!")
            app_config = WirepasTLVAppConfig()

        try:
            ipv6_config = IPV6NetworkConfig.from_bytes(app_config.entries[self.APP_CONFIG_TLV_TYPE_PREFIX])
            # Modify nw_prefix with latest info we have
            ipv6_config.nw_prefix = self.nw_prefix
            # Update off_mesh service if set (keep previous one if already set)
            if self.off_mesh_service is not None:
                ipv6_config.off_mesh_service = self.off_mesh_service
            # Increment nonce
            ipv6_config.increment_nonce()
        except (KeyError, ValueError) as e:
            logging.info(e)
            logging.info("Creating new ipv6 config")
            # Not set already or with wrong fromat
            # Create a new one
            ipv6_config = IPV6NetworkConfig(nw_prefix=self.nw_prefix, off_mesh_service=self.off_mesh_service)

        # Add back the config with modified info
        app_config.add_entry(self.APP_CONFIG_TLV_TYPE_PREFIX,
                             ipv6_config.to_bytes())

        new_config = {}
        new_config["app_config_data"] = app_config.value
        # Not used anymore, can be anything
        new_config["app_config_seq"] = 0
        # Keep old diag value
        new_config["app_config_diag"] = sink_config["app_config_diag"]

        logging.info("Setting new app config with network prefix %s", self.nw_prefix)
        self._sink.write_config(new_config)


class IPV6Transport(BusClient):
    """
    IPV6 transport:
    """

    def __init__(self, external_interface="tap0", off_mesh_service=None) -> None:

        # Initialize local variable
        self.ext_interface = external_interface
        self.wp_interface = "tun_wirepas"
        self.off_mesh_service = off_mesh_service

        # Keep track of sink and their wirepas config
        # IPV6 routing is based on sink address and not its logical id (sink0, sink1,...)
        self.sink_dic = {}

        # Create tun interface (removing it first to cleanup associated rules in case of previous crash)
        self._remove_tun_interface()
        self._create_tun_interface()

        # Get file descriptor for created tun interface
        self.tun = self._get_tun_interface_fd()

        # Get the network prefix associated with the external interface
        self.nw_prefix = self._get_external_prefix()

        logging.info("Network prefix is: %s " % self.nw_prefix)

        # Add a default route for network to external interface
        # Use replace instead of add in case it still exist
        IPV6Transport._execute_cmd("sudo ip -6 route replace %s dev %s" % (self.nw_prefix, self.ext_interface),
                                    True)

        # Initialize super class
        super().__init__()
        self.busThread = Thread(target=self.run)

        # For now add all sinks, but could be reduced to a subset
        for sink in self.sink_manager.get_sinks():
            # Give sink_id even if we have already the sink object
            self._add_sink_entry(sink.sink_id)


    def _add_sink_entry(self, name):
        # Get sink object based on its name (sink0, sink1,...)
        sink = self.sink_manager.get_sink(name)

        try:
            ipv6_sink = IPV6Sink(sink, self.nw_prefix, self.ext_interface, self.off_mesh_service)
        except RuntimeError:
            # Not an issue, it will be added when stack is started
            return

        self.sink_dic[name] = ipv6_sink

        # Add a route for this sink
        self._add_route_to_tun_interface(ipv6_sink.ipv6_sink_prefix)

        self._add_address_to_tun_interface(ipv6_sink.ipv6_interface_address)

        ipv6_sink.start()

    def _remove_sink_entry(self, name):
        try:
            ipv6_sink = self.sink_dic[name]

            self._remove_route_to_tun_interface(ipv6_sink.ipv6_sink_prefix)

            self._remove_address_from_tun_interface(ipv6_sink.ipv6_interface_address)

            ipv6_sink.stop()

            # Remove it from our dic
            del self.sink_dic[name]

        except KeyError:
            logging.error("Sink %s was not in our list, cannot remove it" % name)

    # Inherited methods from BusClient
    def on_sink_connected(self, name):
        # Will be added only if sink is started
        self._add_sink_entry(name)

    def on_sink_disconnected(self, name):
        self._remove_sink_entry(name)

    def on_stack_started(self, name):
        # Stack is started, add it to our list
        self._add_sink_entry(name)

    def on_stack_stopped(self, name):
        # When stack is stopped, do not route traffic to us and consider
        # the sink as being removed
        self._remove_sink_entry(name)

    def on_data_received(
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
        if src_ep == WIREPAS_IPV6_EP and dst_ep == WIREPAS_IPV6_EP:
            logging.info(
                "Ipv6 traffic from wp nw on sink %s FROM %d TO %d Data Size is %d" % (
                sink_id,
                src,
                dst,
                len(data))
            )

            # Update ndproxy based on traffic
            self._add_ndp_entry(sink_id, src)

            # Inject it as is to tun interface
            os.write(self.tun.fileno(), data)


    @classmethod
    def _execute_cmd(cls, cmd, raise_exception=False):
        logging.info("executing cmd: %s" % cmd)
        result = subprocess.run(
            ["%s" % cmd],
            capture_output=True,
            shell=True,
            text=True)

        if raise_exception and result.returncode != 0:
            raise RuntimeError("Return code is not 0: %s" % result.stderr)

        return result.stdout

    def _get_external_prefix(self):
        # For now only get the first prefix from the given interface and consider
        # it as our network prefix

        # Try it 5 times with 1 second delay until interface is ready
        attempts = 5
        while attempts:
            try:
                out = IPV6Transport._execute_cmd(
                    "sudo rdisc6 -q -1 %s" % self.ext_interface, True)
                break
            except RuntimeError as e:
                time.sleep(1)
                attempts = attempts - 1
                if attempts == 0:
                    raise e

        return Ipv6Add.from_srting(out)

    def _remove_tun_interface(self):
        logging.info("Remove tun interface " + self.wp_interface)
        IPV6Transport._execute_cmd(
            "sudo ip tuntap del mode tun dev %s" % self.wp_interface)

    def _create_tun_interface(self):
        logging.info("Create tun interface")
        IPV6Transport._execute_cmd(
            "sudo ip tuntap add mode tun dev %s user wirepas" % self.wp_interface,
            True)

        logging.info("Bring tun interface up")
        IPV6Transport._execute_cmd(
            "sudo ip link set %s up" % self.wp_interface,
            True)

    def _add_route_to_tun_interface(self, sink_prefix):
        IPV6Transport._execute_cmd("sudo ip -6 route add %s dev %s metric 1" % (sink_prefix, self.wp_interface),
                                    True)

    def _remove_route_to_tun_interface(self, sink_prefix):
        IPV6Transport._execute_cmd("sudo ip -6 route del %s dev %s" % (sink_prefix, self.wp_interface),
                                    True)

    def _add_address_to_tun_interface(self, address):
        IPV6Transport._execute_cmd(
            "sudo ip address add %s dev %s" % (address, self.wp_interface),
            True)

    def _remove_address_from_tun_interface(self, address):
        IPV6Transport._execute_cmd(
            "sudo ip address del %s dev %s" % (address, self.wp_interface),
            True)

    def _add_ndp_entry(self, sink_id, node_address):
        try:
            ipv6_sink = self.sink_dic[sink_id]
            ipv6_sink.add_ndp_entry(node_address)
        except KeyError:
            logging.error("Sink %s was not in our list, cannot add ndp entry" % sink_id)

    def _remove_ndp_entry(self, sink_id, node_address):
        try:
            ipv6_sink = self.sink_dic[sink_id]
            ipv6_sink.remove_ndp_entry(node_address)
        except KeyError:
            logging.error("Sink %s was not in our list, cannot remove ndp entry" % sink_id)

    def _get_tun_interface_fd(self):
        # Some constants used to ioctl the device file
        TUNSETIFF = 0x400454ca
        IFF_TUN = 0x0001
        IFF_NO_PI = 0x1000

        # Open TUN device file.
        tun = open('/dev/net/tun', 'r+b', buffering=0)

        ifr = struct.pack('16sH', bytearray(
            self.wp_interface, 'utf-8'), IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(tun, TUNSETIFF, ifr)

        return tun


    def start(self):

        self.busThread.start()

        while True:
            # Wait for packet on TUN device.
            packet = bytearray(os.read(self.tun.fileno(), 2048))

            # Only propagate icmpv6 (58) and UDP traffic (17)
            next_header = packet[6]
            if next_header != 58 and next_header != 17:
                continue

            # Todo, only do it under debug
            try:
                src_addr = Ipv6Add(packet[8:24])
                dst_addr = Ipv6Add(packet[24:40])
            except ValueError:
                logging.error("Cannot parse ipv6 address")

            logging.info("[%s]: %s => %s" % (next_header, src_addr, dst_addr))
            logging.debug("Sink: 0x%x Node: 0x%x" % (dst_addr.wirepas_sink_add, dst_addr.wirepas_node_add))

            # Check if destination address is for our network
            # if not dst_addr.start_with_prefix(self.network_prefix.add[0:8]):
            #    print("Not for us")
            #    continue

            # Do not send multicast packet inside the network
            if dst_addr.start_with_prefix(b'\xff\x02'):
                logging.info("Discard multicast addresses")
                continue

            # Send IPV6 packet from correct sink
            sent = False
            for ipv6_sink in self.sink_dic.values():
                if ipv6_sink.wp_address == dst_addr.wirepas_sink_add:
                    ipv6_sink.send_data(
                        dst_addr.wirepas_node_add,
                        bytes(packet))
                    sent = True
                    break

            if not sent:
                logging.error("No sink with id: %s", dst_addr.wirepas_sink_add)


def str2none(value):
    """ Ensures string to bool conversion """
    if value == "":
        return None
    return value

def main():

    parser = argparse.ArgumentParser(fromfile_prefix_chars='@')

    parser.add_argument(
        "--external_interface",
        default=os.environ.get("WM_IPV6_EXTERNAL_INTERFACE", "tap0"),
        action="store",
        type=str2none,
        help="Ipv6 external interface (where ipv6 prefix is advertized)",
    )

    parser.add_argument(
        "--off_mesh_service",
        default=os.environ.get("WM_IPV6_OFF_MESH_SERVICE", None),
        action="store",
        type=str2none,
        help="Ipv6 off mesh service",
    )

    args = parser.parse_args()
    
    logging.basicConfig(format='%(levelname)s %(asctime)s %(message)s', level=logging.INFO)

    off_mesh_service = None
    if args.off_mesh_service is not None:
        try:
            off_mesh_service = Ipv6Add.from_srting(args.off_mesh_service)
        except ValueError:
            logging.error("Wrong format for ipv6 off mesh service (%s)" % args.off_mesh_service)
            exit(1)

    ipv6_transport = IPV6Transport(external_interface=args.external_interface, off_mesh_service=off_mesh_service)
    # Start transport
    ipv6_transport.start()


if __name__ == "__main__":
    main()
