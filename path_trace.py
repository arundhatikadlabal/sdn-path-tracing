from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet import ethernet

log = core.getLogger()

# Per-switch MAC learning table
mac_to_port = {}

# Store observed path hops
# key: (src_mac, dst_mac)
# value: list of (switch_name, in_port, out_port)
path_table = {}

# Host labels (h1, h2, ...)
host_labels = {}


def get_switch_name(dpid):
    return "s%s" % dpid


def get_host_label(mac):
    mac_str = str(mac)

    if mac_str not in host_labels:
        host_labels[mac_str] = "h%s" % (len(host_labels) + 1)

    return host_labels[mac_str]


def record_hop(src, dst, switch_name, in_port, out_port):
    flow = (str(src), str(dst))

    if flow not in path_table:
        path_table[flow] = []

    hop = (switch_name, in_port, out_port)

    # Avoid duplicate consecutive entries
    if len(path_table[flow]) == 0 or path_table[flow][-1] != hop:
        path_table[flow].append(hop)


def print_path(src, dst):
    flow = (str(src), str(dst))

    if flow not in path_table or len(path_table[flow]) == 0:
        return

    src_label = get_host_label(src)
    dst_label = get_host_label(dst)

    # Unique switches
    seen = set()
    ordered_switches = []

    for hop in path_table[flow]:
        sw = hop[0]
        if sw not in seen:
            seen.add(sw)
            ordered_switches.append(sw)

    # Sort switches properly (s1, s2, s3...)
    ordered_switches.sort(key=lambda x: int(x[1:]))

    full_path = [src_label] + ordered_switches + [dst_label]
    path_str = " -> ".join(full_path)

    log.info("CLEAN PATH: %s", path_str)

    # Print clean flow hops (no duplicates)
    printed = set()
    for sw, in_p, out_p in path_table[flow]:
        if (sw, in_p, out_p) not in printed:
            printed.add((sw, in_p, out_p))
            log.info("%s: in_port=%s -> out_port=%s", sw, in_p, out_p)


def _handle_PacketIn(event):
    packet = event.parsed

    if not packet.parsed:
        return

    dpid = event.connection.dpid
    switch_name = get_switch_name(dpid)

    if dpid not in mac_to_port:
        mac_to_port[dpid] = {}

    in_port = event.port
    src = packet.src
    dst = packet.dst

    # Learn MAC
    mac_to_port[dpid][src] = in_port

    # Forwarding decision
    if dst in mac_to_port[dpid]:
        out_port = mac_to_port[dpid][dst]
    else:
        out_port = of.OFPP_FLOOD

    log.info("SWITCH %s: src=%s dst=%s in_port=%s out_port=%s",
             switch_name, src, dst, in_port, out_port)

    # Send packet
    msg = of.ofp_packet_out()
    msg.data = event.ofp
    msg.actions.append(of.ofp_action_output(port=out_port))
    event.connection.send(msg)

    # Install flow rule
    if out_port != of.OFPP_FLOOD:
        fm = of.ofp_flow_mod()
        fm.match.in_port = in_port
        fm.match.dl_src = src
        fm.match.dl_dst = dst
        fm.idle_timeout = 30
        fm.hard_timeout = 60
        fm.actions.append(of.ofp_action_output(port=out_port))
        event.connection.send(fm)

        log.info("FLOW INSTALLED on %s: match(in_port=%s, src=%s, dst=%s) -> output=%s",
                 switch_name, in_port, src, dst, out_port)

        # Record path
        record_hop(src, dst, switch_name, in_port, out_port)

        # Print clean path
        print_path(src, dst)


def launch():
    core.openflow.addListenerByName("PacketIn", _handle_PacketIn)
    log.info("Path Trace Controller Started")
