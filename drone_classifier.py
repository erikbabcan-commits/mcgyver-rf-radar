#!/usr/bin/env python3
"""
Drone Classifier - pasivny detektor WiFi zariadeni s kategorizaciou.
Bezi na monitormode rozhrani (napr. wlp0s20u2 s D-Link DWA-131).

Instalacia:
    pip install scapy
    sudo iw dev wlp0s20u2 set type monitor
    sudo ip link set wlp0s20u2 up
Spustenie:
    sudo python3 drone_classifier.py -i wlp0s20u2

Je to 100% pasivny nastroj: nic nevysiela, len pocuva.
"""

import argparse
import time
from collections import defaultdict, deque

from scapy.all import rdpcap  # noqa: F401  (jen pro pripad analyzy pcap)
from scapy.layers.dot11 import Dot11, Dot11Beacon, Dot11ProbeReq, RadioTap

# ------------------------------------------------------------------
# Konfiguracia
# ------------------------------------------------------------------

CONF = {
    "beacon_window_s": 10.0,    # okno na vypocet beacon rate
    "drone_beacon_min_rate": 5, # beacony za okno = AP/dron AP
    "phone_probe_gap_max": 8.0, # telefony probuju menej casto
    "rssi_move_delta": 6,       # dBm rozdiel = pohyb
    "rssi_samples": 12,         # kolko RSSI vzoriek drzime na MAC
    "channel_hop_s": 3.0,       # prepinanie kanalov (1-13)
    "channel_range": [1, 6, 11],  # kanaly na 2.4 GHz, posibles: scan
}

# Zname OUI prefixy dronov a FPV vybavenia
DRONE_OUI = {
    "60:60:1f": "Ryze Tello",
    "80:e1:26": "Parrot SA",
    "90:03:b7": "Intel (napr. Aero)",
    "a4:cf:12": "DJI (niektore verzie)",
    "d8:96:95": "DJI (ADS-B/RemoteID moduly)",
    "34:80:b3": "DJI",
    "44:72:60": "Hubsan",
    "0c:28:bd": "Skydio",
}

WELL_KNOWN_AP_HINT = ("tplink", "tp-link", "dlink", "d-link",
                      "huawei", "zxyel", "asus", "netgear", "orange",
                      "telekom", "ssid")  # typicke routry ani tak

AP_VENDORS_BLACKLIST = {
    "3c:78:95": "TP-Link Systems",
    "b0:4e:26": "TP-Link",
    "40:86:cb": "D-Link",
    "f4:f2:6d": "Zyxel",
    "ac:9e:17": "Huawei",
    "dc:a2:66": "D-Link",
}


def is_locally_administered(mac: str) -> bool:
    """MAC, ktora ma nastaveny 'locally administered' bit (randomizovana).
    Kandidat: telefon v inkognito mode, alebo dron/dongle."""
    return bool(int(mac.replace(":", "")[:2], 16) & 0x02)


def oui_lookup(mac: str) -> str:
    """Vrati string s vendorom, ak vie."""
    prefix = mac.lower()[:8]
    mac_prefix = prefix.replace(":", "")
    vendor_map = {k.replace(":", ""): v
                  for k, v in AP_VENDORS_BLACKLIST.items()}
    if mac_prefix in vendor_map:
        return vendor_map[mac_prefix]
    vendor_map2 = {k.replace(":", ""): v for k, v in DRONE_OUI.items()}
    if mac_prefix in vendor_map2:
        return vendor_map2[mac_prefix]
    return "Neznamy vendor"


class DeviceTracker:
    """Sleduje kazdu MAC: beacony, probe requesty, RSSI signal."""

    def __init__(self, conf):
        self.conf = conf
        self.beacons = defaultdict(lambda: deque(maxlen=50))
        self.probes = defaultdict(lambda: deque(maxlen=50))
        self.rssi = defaultdict(lambda: deque(maxlen=conf["rssi_samples"]))
        self.ssid = {}


def had_move(tracker, mac: str) -> bool:
    """RSSI kolisanie naznacuje pohyb zariadenia."""
    samples = list(tracker.rssi[mac])
    if len(samples) < 4:
        return False
    return (max(samples) - min(samples)) >= tracker.conf["rssi_move_delta"]


def beacon_rate(tracker, mac: str, now: float) -> int:
    """Kolko beaconov za okno (AP/dron AP beaconuje 1x/100 ms)."""
    window = tracker.conf["beacon_window_s"]
    return sum(1 for t in tracker.beacons[mac] if now - t <= window)


def probe_frequency(tracker, mac: str, now: float) -> float:
    """Priemerny interval medzi probe requestami."""
    times = list(tracker.probes[mac])
    if len(times) < 2:
        return float("inf")
    return (times[-1] - times[0]) / (len(times) - 1)


def classify(tracker, mac: str, now: float) -> str:
    """Kategorizacia zariadenia podla nasho planu:
    - STANDARD ROUTER (SSID viden, pravidelne beacony, bez pohybu)
    - PHONE (probe requesty, randomizovana MAC, bez pravidelnych beaconov)
    - DRONE_CANDIDATE (skryte SSID + pravidelne beacony + RSSI kolisanie)
    - DRONE_VENDOR (OUI zhoda so znamymi dronmi)
    """
    vendor = oui_lookup(mac)
    b_rate = beacon_rate(tracker, mac, now)
    moving = had_move(tracker, mac)
    hidden = tracker.ssid.get(mac, "<skryte>") in ("<skryte>", "")

    if any(vendor == v for v in DRONE_OUI.values()):
        return "DRONE_VENDOR"
    if ((hidden and b_rate >= tracker.conf["drone_beacon_min_rate"])
            and moving):
        return "DRONE_CANDIDATE"
    if b_rate >= tracker.conf["drone_beacon_min_rate"]:
        return "STANDARD_AP_ROUTER"
    # ostatne: telefón / tablet s randomizovanou MAC
    if is_locally_administered(mac) and not moving:
        return "PHONE (randomized MAC)"
    return "OTHER_DEVICE"


def main():
    parser = argparse.ArgumentParser(description="Drone Classifier")
    parser.add_argument("-i", help="Monitor mode interface (napr. wlp0s20u2)")
    args = parser.parse_args()
    iface = args.i or "wlp0s20u2"

    tracker = DeviceTracker(CONF)

    def handle_pkt(pkt):
        if not pkt.haslayer(Dot11):
            return
        dot11 = pkt[Dot11]
        now = time.time()
        mac_src = None
        if dot11.type == 0:  # management frame
            if dot11.subtype == 8:  # beacon
                mac_src = dot11.addr2
                p = pkt[Dot11Beacon]
                ssid = p.info.decode(errors="ignore") or "<skryte>"
                tracker.ssid[mac_src] = ssid
                tracker.beacons[mac_src].append(now)
                hidden = (not p.info)
                if hidden:
                    tracker.ssid[mac_src] = "<skryte>"
            elif dot11.subtype == 4:  # probe request
                mac_src = dot11.addr2
                tracker.probes[mac_src].append(now)
        if mac_src is None:
            return
        # RSSI
        rssi = None
        if pkt.haslayer(RadioTap):
            try:
                sig = pkt[RadioTap].dBm_AntSignal
                if sig is not None:
                    rssi = sig if sig < 128 else sig - 256
            except (TypeError, AttributeError):
                rssi = None
        if rssi is None:
            rssi = getattr(pkt, "dBm_AntSignal", None)

        if rssi is not None:
            tracker.rssi[mac_src].append(rssi)

        cls = classify(tracker, mac_src, now)
        ssid = tracker.ssid.get(mac_src, "<skryte>")
        vendor = oui_lookup(mac_src)
        rssi_now = list(tracker.rssi[mac_src])[-1] if tracker.rssi[mac_src] else "?"
        print(f"{cls:>20} | {mac_src} | {ssid:18} | {vendor:20} | {rssi_now} dBm")

    # hlavny loop s channel hopping
    from scapy.all import sniff
    hop_list = CONF["channel_range"]
    hop_idx = 0

    def hop_channels():
        nonlocal hop_idx
        import subprocess
        ch = hop_list[hop_idx % len(hop_list)]
        hop_idx += 1
        subprocess.run(["iw", "dev", iface, "set", "channel", str(ch)],
                       check=False)

    print(f"Pocuvam na {iface} (kanaly {hop_list})... Ctrl+C na ukoncenie.")
    hop_channels()
    last_hop = time.time()
    try:
        while True:
            sniff(iface=iface, prn=handle_pkt, timeout=CONF["channel_hop_s"],
                  store=False)
            if time.time() - last_hop >= CONF["channel_hop_s"]:
                hop_channels()
                last_hop = time.time()
    except KeyboardInterrupt:
        print("\nKonec. Zhrnutie zaznamenanych zariadeni:")
        for mac, ssid in tracker.ssid.items():
            print(f"  {mac} | {ssid} | {classify(tracker, mac, time.time())}")


if __name__ == "__main__":
    main()
