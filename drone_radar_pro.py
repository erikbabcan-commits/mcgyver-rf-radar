#!/usr/bin/env python3
"""
=============================================================================
🛰️ DRONE RADAR PRO - ASTM F3411, OpenDroneID & DJI DroneID Interceptor
=============================================================================
Vysoko presný pasívny rádiový radar pre 2.4 GHz s filtrom domáceho šumu,
dekódovaním ASTM F3411 (FA:0B:BC), francúzskeho DRI (6A:5C:35) a DJI DroneID.
"""

import sys
import os
import time
import subprocess
import threading
from collections import deque
from datetime import datetime

# Domáci whitelist (zariadenia používateľa, ktoré nebudú spamovať radar)
HOME_MACS = {
    "b0:19:21:bb:b6:ad", "b2:19:21:1b:b6:ad", "b2:19:21:2b:b6:ad",
    "b2:19:21:8b:b6:ad", "b2:19:21:9b:b6:ad", "4c:a9:19:e3:03:4d",
    "fc:3c:d7:81:65:fc", "82:4a:38:1d:05:80", "40:86:cb:d2:2b:4c"
}

# Známe Drone ID Vendor Tags (VSIE)
RID_TAG_OUIS = {
    "fa:0b:bc": "ASTM F3411 / OpenDroneID (EÚ & USA)",
    "6a:5c:35": "French DGAC Remote ID",
    "00:26:7e": "DJI DroneID / Parrot",
    "26:37:12": "DJI Proprietary DroneID",
    "fa:0b:00": "OpenDroneID Standard"
}

# Známe OUI výrobcov dronov
DRONE_MAC_PREFIXES = {
    "60:60:1f": "DJI Innovations",
    "48:a4:93": "DJI Innovations",
    "e4:70:b8": "DJI Innovations",
    "34:d2:62": "DJI / Ryze Tello",
    "08:ea:40": "DJI Innovations",
    "70:b3:d5": "DJI Innovations",
    "00:26:7e": "Parrot Drones",
    "90:03:b7": "Parrot Drones",
    "a0:14:3d": "Parrot Drones",
    "00:12:1c": "Parrot Drones",
    "e0:b6:f5": "Yuneec",
    "2c:16:db": "Autel Robotics",
    "68:54:5a": "Autel Robotics",
    "ec:74:e7": "Skydio",
    "80:65:99": "Espressif (ELRS / VTX / DIY Drone)",
    "bc:dd:c2": "Espressif (ELRS / VTX / DIY Drone)",
    "ec:fa:bc": "Espressif (ELRS / VTX / DIY Drone)",
    "24:0a:c4": "Espressif (ELRS / VTX / DIY Drone)",
    "30:ae:a4": "Espressif (ELRS / VTX / DIY Drone)"
}

DRONE_SSID_KEYWORDS = [
    "dji", "tello", "parrot", "bebop", "mavic", "phantom", "spark",
    "autel", "skydio", "yuneec", "fpv", "drone", "elrs", "expresslrs", "remoteid"
]

def decode_ssid(val):
    if not val or val == "<MISSING>":
        return "<skryté>"
    # Ak tshark vyhodil hex formát (napr. 54502d4c69...)
    if len(val) >= 4 and len(val) % 2 == 0:
        try:
            b = bytes.fromhex(val)
            decoded = b.decode("utf-8", errors="ignore")
            if any(32 <= ord(c) < 127 for c in decoded):
                return decoded
        except Exception:
            pass
    return val

def clear_screen():
    print("\033[H\033[J", end="")

class ChannelHopper(threading.Thread):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        # Prednostne skenujeme najčastejšie kanály 1, 6, 11, a potom ostatné
        self.channels = [1, 6, 11, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13]
        self.current_channel = 1
        self.stop_event = threading.Event()
        self.daemon = True

    def run(self):
        while not self.stop_event.is_set():
            for ch in self.channels:
                if self.stop_event.is_set():
                    break
                subprocess.run(["iw", "dev", self.iface, "set", "channel", str(ch)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.current_channel = ch
                time.sleep(0.25)

    def stop(self):
        self.stop_event.set()

def main():
    if os.geteuid() != 0:
        print("[!] Tento skript musí bežať s právami roota: sudo python3", sys.argv[0])
        sys.exit(1)

    iface = "wlp0s20u2"
    log_file = "/tmp/drone_radar.log"
    log_fp = open(log_file, "w", buffering=1)

    print("\033[1;32m[*] Spúšťam Drone Radar PRO na rozhraní " + iface + "...\033[0m")
    hopper = ChannelHopper(iface)
    hopper.start()

    cmd = [
        "tshark", "-i", iface, "-l", "-n",
        "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "radiotap.dbm_antsignal",
        "-e", "wlan.fc.type_subtype",
        "-e", "wlan.sa",
        "-e", "wlan.bssid",
        "-e", "wlan.ssid",
        "-e", "wlan.sa.oui_resolved",
        "-e", "wlan.tag.oui"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    drones = {}         # mac: dict
    close_unknown = {}  # mac: dict
    last_ui_update = 0

    clear_screen()
    print("🛰️ DRONE RADAR PRO AKTÍVNY... Počúvam rádiové pásmo.")

    try:
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue

            epoch = parts[0]
            rssi_str = parts[1]
            subtype = parts[2]
            src_mac = parts[3].lower()
            bssid = parts[4].lower() if len(parts) > 4 else ""
            raw_ssid = parts[5] if len(parts) > 5 else ""
            vendor = parts[6] if len(parts) > 6 else ""
            tag_ouis = parts[7].lower() if len(parts) > 7 else ""

            # Filter domáceho routera a smart zariadení používateľa
            if src_mac in HOME_MACS or bssid in HOME_MACS:
                continue
            if any(src_mac.startswith(p) for p in ["b0:19:21", "b2:19:21", "4c:a9:19", "fc:3c:d7"]):
                continue

            try:
                rssi = int(rssi_str.split(",")[0])
            except Exception:
                continue

            ssid = decode_ssid(raw_ssid)

            # Analýza Remote ID a Dronových podpisov
            is_drone = False
            drone_reason = ""

            # 1. Kontrola ASTM F3411 / OpenDroneID VSIE tagu (Najvyššia spoľahlivosť)
            for rid_oui, rid_desc in RID_TAG_OUIS.items():
                if rid_oui in tag_ouis:
                    is_drone = True
                    drone_reason = f"RemoteID ({rid_desc})"
                    break

            # 2. Kontrola MAC OUI
            if not is_drone:
                prefix = src_mac[:8]
                if prefix in DRONE_MAC_PREFIXES:
                    is_drone = True
                    drone_reason = f"OUI ({DRONE_MAC_PREFIXES[prefix]})"
                elif any(v in vendor.lower() for v in ["dji", "parrot", "yuneec", "autel", "skydio"]):
                    is_drone = True
                    drone_reason = f"Vendor ({vendor})"

            # 3. Kontrola SSID kľúčových slov
            if not is_drone and ssid and ssid != "<skryté>":
                for kw in DRONE_SSID_KEYWORDS:
                    if kw in ssid.lower():
                        is_drone = True
                        drone_reason = f"SSID-Kľúč ({kw.upper()})"
                        break

            now = datetime.now()

            # Zápis do logu
            log_line = f"[{now.strftime('%H:%M:%S')}] Ch:{hopper.current_channel} MAC:{src_mac} RSSI:{rssi}dBm Type:{drone_reason or 'Non-Drone'} SSID:{ssid}\n"
            log_fp.write(log_line)

            if is_drone:
                if src_mac not in drones:
                    # Zvukový poplach pri novom záchyte
                    print("\a", end="", flush=True)
                drones[src_mac] = {
                    "reason": drone_reason,
                    "ssid": ssid,
                    "rssi": rssi,
                    "channel": hopper.current_channel,
                    "last_seen": now,
                    "count": drones.get(src_mac, {}).get("count", 0) + 1
                }
            elif rssi > -95:  # Extrémna citlivosť na hranici šumu - dosah 250 až 300 metrov
                close_unknown[src_mac] = {
                    "vendor": vendor or "Neznámy",
                    "ssid": ssid,
                    "rssi": rssi,
                    "channel": hopper.current_channel,
                    "last_seen": now
                }

            # Obnova obrazovky každú 1 sekundu
            if time.time() - last_ui_update > 1.0:
                last_ui_update = time.time()
                clear_screen()
                print("="*78)
                print(f"🛰️  DRONE RADAR PRO (ASTM F3411 & DJI Interceptor) | Kanál: {hopper.current_channel:2d}")
                print(f"    Rozhranie: {iface} (Monitor) | Záchytov dronov: {len(drones)} | Čas: {now.strftime('%H:%M:%S')}")
                print("="*78)

                if drones:
                    print("\033[1;31m🚨 DETEGOVANÉ DRONY A REMOTE ID VYSIELAČE:\033[0m")
                    print(f"{'TYP / DÔVOD':<25} {'MAC ADRESA':<19} {'SSID':<15} {'RSSI':<9} {'VZDIALENOSŤ':<10}")
                    print("-" * 78)
                    for mac, d in sorted(drones.items(), key=lambda x: x[1]["last_seen"], reverse=True):
                        r = d['rssi']
                        if r > -55:
                            dist = "🔥 <25m"
                        elif r > -70:
                            dist = "⚡ 25-60m"
                        elif r > -82:
                            dist = "🟡 60-120m"
                        elif r > -90:
                            dist = "📡 120-200m"
                        else:
                            dist = "🔭 200-250m+"
                        print(f"\033[1;31m{d['reason'][:23]:<25} {mac:<19} {d['ssid'][:13]:<15} {str(r)+' dBm':<9} {dist:<10}\033[0m")
                    print("-" * 78)
                else:
                    print("\033[1;32m[+] Éter monitorovaný: Žiadny známy RemoteID ani DJI Wi-Fi maják nezachytený.\033[0m")

                print("\n📡 ZARIADENIA V PERIMETRI 250m+ (Extrémny dosah, Filtrovaný domáci router):")
                print(f"{'VÝROBCA':<20} {'MAC ADRESA':<19} {'SSID':<15} {'RSSI':<9} {'ODHAD VZDIAL.'}")
                print("-" * 78)
                recent_unknown = [
                    (m, u) for m, u in close_unknown.items()
                    if (now - u["last_seen"]).total_seconds() < 20
                ]
                if recent_unknown:
                    for mac, u in sorted(recent_unknown, key=lambda x: x[1]["rssi"], reverse=True)[:18]:
                        r = u['rssi']
                        if r > -55:
                            status = "🔥 <25m"
                        elif r > -70:
                            status = "⚡ 25-60m"
                        elif r > -82:
                            status = "🟡 60-120m"
                        elif r > -90:
                            status = "📡 120-200m"
                        else:
                            status = "🔭 200-250m+"
                        print(f"{u['vendor'][:18]:<20} {mac:<19} {u['ssid'][:13]:<15} {str(r)+' dBm':<9} \033[1;33m{status:<10}\033[0m")
                else:
                    print("  (Žiadne cudzie zariadenia so signálom silnejším ako -95 dBm)")

                print("-" * 78)
                print("Stlačte Ctrl+C pre ukončenie...")

    except KeyboardInterrupt:
        print("\n[*] Radar ukončený.")
    finally:
        hopper.stop()
        proc.terminate()
        log_fp.close()

if __name__ == "__main__":
    main()
