#!/usr/bin/env python3
"""
=============================================================================
McGyver Wi-Fi Suite - Multifunkčný rádiový nástroj pre 2.4 GHz
=============================================================================
Hardware: D-Link DWA-137 (RTL8192EU) + Domáci Wi-Fi Router
Autor: Antigravity & User

Moduly:
  1. Pasívny dronový radar (Remote ID & Drone Sniffer)
  3. Rádiový zameriavač (Radio Direction Finder / RSSI Tracker)
  4. Neviditeľný pohybový radar (Wi-Fi Human Sensing / Fresnel Motion Detector)
  5. Detektor prítomnosti smartfónov (Probe Request Hunter)
  7. Strážny pes proti Evil Twin (Rogue AP Watchdog)
=============================================================================
"""

import sys
import os
import time
import subprocess
import threading
import math
from collections import deque, defaultdict
from datetime import datetime

# Známe OUI databázy pre drony a RC rádiá
DRONE_OUIS = {
    "60:60:1f": "DJI",
    "48:a4:93": "DJI",
    "e4:70:b8": "DJI",
    "34:d2:62": "DJI / Ryze Tello",
    "08:ea:40": "DJI",
    "00:26:7e": "Parrot",
    "90:03:b7": "Parrot",
    "a0:14:3d": "Parrot",
    "00:12:1c": "Parrot",
    "e0:b6:f5": "Yuneec",
    "2c:16:db": "Autel Robotics",
    "68:54:5a": "Autel Robotics",
    "ec:74:e7": "Skydio",
    "80:65:99": "Espressif (ELRS / DIY Drone)",
    "bc:dd:c2": "Espressif (ELRS / DIY Drone)",
    "ec:fa:bc": "Espressif (ELRS / DIY Drone)",
    "24:0a:c4": "Espressif (ELRS / DIY Drone)",
    "30:ae:a4": "Espressif (ELRS / DIY Drone)"
}

DRONE_SSID_KEYWORDS = [
    "dji", "tello", "parrot", "bebop", "mavic", "phantom",
    "autel", "skydio", "yuneec", "fpv", "drone", "elrs", "expresslrs"
]

def clear_screen():
    print("\033[H\033[J", end="")

def check_root():
    if os.geteuid() != 0:
        print("[!] UPOZORNENIE: Tento skript vyžaduje práva roota na odchytávanie surových paketov a zmenu kanálov.")
        print("    Spustite prosím: sudo python3", sys.argv[0])
        sys.exit(1)

def get_wireless_interfaces():
    interfaces = []
    try:
        out = subprocess.check_output(["iw", "dev"], text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                interfaces.append(line.split()[1])
    except Exception as e:
        print("[!] Chyba pri zisťovaní rozhraní cez 'iw':", e)
    return interfaces

def get_interface_mode(iface):
    try:
        out = subprocess.check_output(["iw", "dev", iface, "info"], text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("type "):
                return line.split()[1]
    except Exception:
        pass
    return "unknown"

def set_monitor_mode(iface, enable=True):
    mode = "monitor" if enable else "managed"
    print(f"[*] Prepínam rozhranie {iface} do režimu '{mode}'...")
    if enable:
        # Odpojíme od NetworkManageru, aby neprepínal rozhranie späť do AP/Hotspotu
        subprocess.run(["nmcli", "device", "disconnect", iface], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["nmcli", "device", "set", iface, "managed", "no"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ip", "link", "set", iface, "down"], check=True)
        subprocess.run(["iw", "dev", iface, "set", "type", "monitor"], check=True)
        subprocess.run(["ip", "link", "set", iface, "up"], check=True)
    else:
        subprocess.run(["ip", "link", "set", iface, "down"], check=True)
        subprocess.run(["iw", "dev", iface, "set", "type", "managed"], check=True)
        subprocess.run(["ip", "link", "set", iface, "up"], check=True)
        subprocess.run(["nmcli", "device", "set", iface, "managed", "yes"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[+] Hotovo. Aktuálny režim {iface}: {get_interface_mode(iface)}")

def set_channel(iface, channel):
    try:
        subprocess.run(["iw", "dev", iface, "set", "channel", str(channel)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

class ChannelHopper(threading.Thread):
    def __init__(self, iface, channels=None, interval=0.35):
        super().__init__()
        self.iface = iface
        self.channels = channels or [1, 6, 11, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13]
        self.interval = interval
        self.stop_event = threading.Event()
        self.current_channel = self.channels[0]
        self.daemon = True

    def run(self):
        while not self.stop_event.is_set():
            for ch in self.channels:
                if self.stop_event.is_set():
                    break
                set_channel(self.iface, ch)
                self.current_channel = ch
                time.sleep(self.interval)

    def stop(self):
        self.stop_event.set()

def start_tshark_capture(iface, display_filter=None):
    cmd = [
        "tshark", "-i", iface, "-l", "-n",
        "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "radiotap.dbm_antsignal",
        "-e", "wlan.fc.type_subtype",
        "-e", "wlan.sa",
        "-e", "wlan.bssid",
        "-e", "wlan.ssid",
        "-e", "wlan.sa.oui_resolved"
    ]
    if display_filter:
        cmd.extend(["-Y", display_filter])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1
    )
    return process

# =============================================================================
# MODUL 1: DRONOVÝ RADAR (Drone & RemoteID Sniffer)
# =============================================================================
def run_drone_radar(iface):
    print("\n" + "="*70)
    print("🛰️  MODUL 1: PASÍVNY DRONOVÝ RADAR (Drone & Remote ID Sniffer)")
    print("="*70)
    print("[*] Spúšťam channel hopping cez kanály 1 až 13...")
    hopper = ChannelHopper(iface)
    hopper.start()

    print("[*] Počúvam rádiové pásmo. Pre ukončenie stlačte Ctrl+C...")
    proc = start_tshark_capture(iface)

    detected_drones = {}  # mac: dict(vendor, ssid, last_seen, max_rssi, channel, count)
    last_print = 0

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
            ssid = parts[5] if len(parts) > 5 else ""
            oui_resolved = parts[6] if len(parts) > 6 else ""

            # Spracovanie RSSI
            try:
                rssi = int(rssi_str.split(",")[0])
            except (ValueError, IndexError):
                rssi = -99

            # Detekcia dronu podľa OUI alebo SSID
            is_drone = False
            drone_vendor = None

            # 1. Kontrola podľa MAC prefixu
            mac_prefix = src_mac[:8]
            if mac_prefix in DRONE_OUIS:
                is_drone = True
                drone_vendor = DRONE_OUIS[mac_prefix]
            elif any(d in oui_resolved.lower() for d in ["dji", "parrot", "yuneec", "autel", "skydio"]):
                is_drone = True
                drone_vendor = oui_resolved

            # 2. Kontrola podľa SSID
            if ssid:
                for kw in DRONE_SSID_KEYWORDS:
                    if kw in ssid.lower():
                        is_drone = True
                        if not drone_vendor:
                            drone_vendor = f"SSID-Match ({kw.upper()})"
                        break

            if is_drone:
                now = datetime.now()
                if src_mac not in detected_drones:
                    print(f"\a\n[🚨 ALARM - NOVÝ DRON!] Nájdené: {drone_vendor} | MAC: {src_mac} | SSID: {ssid or '<skryté>'} | RSSI: {rssi} dBm (Ch: {hopper.current_channel})")
                    detected_drones[src_mac] = {
                        "vendor": drone_vendor,
                        "ssid": ssid,
                        "rssi": rssi,
                        "channel": hopper.current_channel,
                        "first_seen": now,
                        "last_seen": now,
                        "count": 1
                    }
                else:
                    d = detected_drones[src_mac]
                    d["last_seen"] = now
                    d["rssi"] = rssi
                    d["channel"] = hopper.current_channel
                    d["count"] += 1
                    if ssid and not d["ssid"]:
                        d["ssid"] = ssid

            # Obnova displeja každé 2 sekundy
            if time.time() - last_print > 2:
                last_print = time.time()
                clear_screen()
                print("="*75)
                print(f"🛰️  PASÍVNY DRONOVÝ RADAR | Aktuálny kanál: {hopper.current_channel} | Zachytené drony: {len(detected_drones)}")
                print("="*75)
                print(f"{'VÝROBCA/TYP':<22} {'MAC ADRESA':<19} {'SSID':<15} {'RSSI':<9} {'ODHAD VZDIAL.':<12}")
                print("-" * 75)
                for mac, d in sorted(detected_drones.items(), key=lambda x: x[1]["last_seen"], reverse=True):
                    # Odhad vzdialenosti
                    r = d['rssi']
                    if r > -55:
                        dist_est = "Veľmi blízko (<30m)"
                    elif r > -70:
                        dist_est = "Blízko (30-100m)"
                    elif r > -85:
                        dist_est = "Stredne (100-300m)"
                    else:
                        dist_est = "Ďaleko (>300m)"

                    print(f"{d['vendor'][:20]:<22} {mac:<19} {(d['ssid'] or '<none>')[:13]:<15} {str(r)+' dBm':<9} {dist_est:<12}")
                print("-" * 75)
                print("Stlačte Ctrl+C pre návrat do hlavného menu...")

    except KeyboardInterrupt:
        print("\n[*] Zastavujem radar...")
    finally:
        hopper.stop()
        proc.terminate()

# =============================================================================
# MODUL 3: RÁDIOVÝ ZAMERIAVAČ (Radio Direction Finder / RSSI Tracker)
# =============================================================================
def run_direction_finder(iface):
    print("\n" + "="*70)
    print("🎯  MODUL 3: RÁDIOVÝ ZAMERIAVAČ (Radio Direction Finder / Signal Tracker)")
    print("="*70)
    print("Tento nástroj meria silu signálu (RSSI) vybraného cieľa.")
    print("Postup: Na D-Link nasaďte smerový reflektor (Windsurfer/plechovku) a otáčajte anténou.")
    print("V smere s najvyššou silou signálu (PEAK) sa nachádza cieľové zariadenie.\n")

    target = input("Zadajte cieľovú MAC adresu alebo názov siete (SSID): ").strip().lower()
    if not target:
        print("[!] Nezadaný žiadny cieľ.")
        return

    ch_input = input("Zadajte kanál cieľa (1-13) [ak neviete, nechajte prázdne pre auto-hop]: ").strip()
    fixed_channel = None
    if ch_input.isdigit() and 1 <= int(ch_input) <= 13:
        fixed_channel = int(ch_input)
        set_channel(iface, fixed_channel)
        hopper = None
        print(f"[+] Kanál uzamknutý na: {fixed_channel}")
    else:
        print("[*] Zapínam prehľadávanie kanálov...")
        hopper = ChannelHopper(iface)
        hopper.start()

    proc = start_tshark_capture(iface)
    peak_rssi = -120
    last_update = time.time()
    current_rssi = -100

    try:
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue

            rssi_str = parts[1]
            src_mac = parts[3].lower()
            bssid = parts[4].lower() if len(parts) > 4 else ""
            ssid = parts[5].lower() if len(parts) > 5 else ""

            match = False
            if target in src_mac or target in bssid:
                match = True
            elif target in ssid:
                match = True

            if match:
                try:
                    rssi = int(rssi_str.split(",")[0])
                except (ValueError, IndexError):
                    continue

                current_rssi = rssi
                if current_rssi > peak_rssi:
                    peak_rssi = current_rssi
                last_update = time.time()

                # Vykreslenie VU-metra
                # Mapovanie RSSI: -95 dBm (0%) až -30 dBm (100%)
                pct = max(0, min(100, int((current_rssi - (-95)) / ((-30) - (-95)) * 100)))
                bar_len = 35
                filled = int(bar_len * (pct / 100))
                bar = "█" * filled + "░" * (bar_len - filled)

                # Indikácia blízkosti
                if current_rssi > -45:
                    status = "🔥🔥🔥 EXTRÉMNE BLÍZKO (Horúco!)"
                elif current_rssi > -60:
                    status = "🔥 BLÍZKO"
                elif current_rssi > -75:
                    status = "🟡 STREDNÁ VZDIALENOSŤ"
                else:
                    status = "❄️ ĎALEKO (Slabý signál)"

                clear_screen()
                print("="*70)
                print(f"🎯  RÁDIOVÝ ZAMERIAVAČ | Cieľ: {target}")
                print(f"    Kanál: {fixed_channel if fixed_channel else hopper.current_channel} | Odchyt MAC: {src_mac}")
                print("="*70)
                print(f"\n  AKTUÁLNA SILA SIGNÁLU: {current_rssi} dBm")
                print(f"  [{bar}] {pct}%\n")
                print(f"  MAXIMÁLNA ZAZNAMENANÁ ŠPIČKA (PEAK): {peak_rssi} dBm")
                print(f"  STATUS: {status}\n")
                print("-" * 70)
                print("🧭 Inštrukcia: Pomaly otáčajte smerovú anténu o 360°.")
                print("   Smer, kde ukazovateľ dosiahne maximum (Peak), je presná poloha cieľa!")
                print("   Stlačte Ctrl+C pre návrat do menu.")

    except KeyboardInterrupt:
        print("\n[*] Ukončujem zameriavač...")
    finally:
        if hopper:
            hopper.stop()
        proc.terminate()

# =============================================================================
# MODUL 4: NEVIDITEĽNÝ POHYBOVÝ RADAR (Wi-Fi Sensing / Fresnel Intrusion Alarm)
# =============================================================================
def run_motion_detector(iface):
    print("\n" + "="*70)
    print("👁️  MODUL 4: NEVIDITEĽNÝ WI-FI POHYBOVÝ RADAR (Human Sensing)")
    print("="*70)
    print("Tento nástroj sleduje kolísanie signálu z vášho domáceho routera.")
    print("Keď človek prejde medzi routerom a D-Link anténou, telo absorbuje 2.4 GHz vlny")
    print("a spôsobí anomáliu v rozptyle signálu (Fresnelova zóna).\n")

    router_ssid = input("Zadajte SSID vášho domáceho routera (alebo MAC): ").strip()
    if not router_ssid:
        print("[!] Nezadaný identifikátor routera.")
        return

    ch_input = input("Zadajte kanál routera (1-13) [odporúčané pozrieť v routeri, napr. 6]: ").strip()
    if ch_input.isdigit():
        target_ch = int(ch_input)
    else:
        target_ch = 6
    set_channel(iface, target_ch)
    print(f"[+] Kanál uzamknutý na {target_ch}.")

    proc = start_tshark_capture(iface, display_filter="wlan.fc.type_subtype == 8")

    print("\n[⏳ KROK 1: KALIBRÁCIA POKOJA]")
    print("Prosím, zostaňte 7 sekúnd v pokoji (žiadny pohyb medzi routerom a anténou)...")

    calibration_samples = []
    cal_start = time.time()

    for line in proc.stdout:
        parts = line.strip().split("\t")
        if len(parts) < 6:
            continue
        rssi_str = parts[1]
        bssid = parts[4].lower()
        ssid = parts[5]

        if router_ssid.lower() in ssid.lower() or router_ssid.lower() in bssid:
            try:
                rssi = int(rssi_str.split(",")[0])
                calibration_samples.append(rssi)
            except Exception:
                continue

        if time.time() - cal_start > 7 and len(calibration_samples) >= 15:
            break

    if len(calibration_samples) < 10:
        print("[!] Nedostatok paketov z routera pre kalibráciu. Skontrolujte kanál alebo SSID.")
        proc.terminate()
        return

    base_avg = sum(calibration_samples) / len(calibration_samples)
    base_std = math.sqrt(sum((x - base_avg) ** 2 for x in calibration_samples) / len(calibration_samples))
    threshold = max(2.5, base_std * 2.8)

    print(f"[+] Kalibrácia úspešná!")
    print(f"    Pokojový priemer: {base_avg:.1f} dBm | Šum pozadia (StdDev): {base_std:.2f} dBm")
    print(f"    Citlivosť detekcie (Prah): ±{threshold:.2f} dBm\n")
    print("[*] STRÁŽNY SYSTÉM JE AKTÍVNY! (Pohnite sa v miestnosti a sledujte reakciu)")
    time.sleep(2)

    window = deque(maxlen=20)
    alarm_count = 0

    try:
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) < 6:
                continue
            rssi_str = parts[1]
            bssid = parts[4].lower()
            ssid = parts[5]

            if router_ssid.lower() in ssid.lower() or router_ssid.lower() in bssid:
                try:
                    rssi = int(rssi_str.split(",")[0])
                    window.append(rssi)
                except Exception:
                    continue

                if len(window) >= 10:
                    curr_avg = sum(window) / len(window)
                    curr_std = math.sqrt(sum((x - curr_avg) ** 2 for x in window) / len(window))
                    delta = abs(curr_avg - base_avg)

                    # Detekcia pohybu
                    is_motion = (curr_std > threshold) or (delta > threshold)
                    meter_val = int(min(curr_std * 5, 25))
                    meter = "!" * meter_val + " " * (25 - meter_val)

                    if is_motion:
                        alarm_count += 1
                        print(f"\a🚨 [POHYB DETEGOVANÝ!] Odchýlka: {curr_std:.2f} dBm | Rozdiel od pokoja: {delta:.1f} dBm | RSSI: {rssi} dBm")
                    else:
                        print(f"\r🛡️  Kľud v priestore  | Rozptyl: [{meter}] ({curr_std:.2f} dBm) | RSSI: {rssi} dBm", end="")

    except KeyboardInterrupt:
        print("\n[*] Ukončujem pohybový senzor...")
    finally:
        proc.terminate()

# =============================================================================
# BONUS MODUL 5: DETEKTOR SMARTFÓNOV (Probe Request Hunter)
# =============================================================================
def run_probe_sniffer(iface):
    print("\n" + "="*70)
    print("📱  BONUS MODUL 5: DETEKTOR PRÍTOMNOSTI SMARTFÓNOV (Probe Request Hunter)")
    print("="*70)
    print("Zobrazuje telefóny v okolí a siete, ktoré hľadajú v éteri.")
    print("Umožňuje zistiť prítomnosť osôb pri dverách alebo v objekte.\n")

    hopper = ChannelHopper(iface)
    hopper.start()

    # wlan.fc.type_subtype == 4 je Probe Request
    proc = start_tshark_capture(iface, display_filter="wlan.fc.type_subtype == 4")
    probes = defaultdict(set)
    device_rssi = {}
    device_vendor = {}

    try:
        last_print = 0
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue

            rssi_str = parts[1]
            src_mac = parts[3].lower()
            ssid = parts[5] if len(parts) > 5 else ""
            vendor = parts[6] if len(parts) > 6 else ""

            try:
                rssi = int(rssi_str.split(",")[0])
            except Exception:
                rssi = -99

            if ssid:
                probes[src_mac].add(ssid)
            device_rssi[src_mac] = rssi
            if vendor:
                device_vendor[src_mac] = vendor

            if time.time() - last_print > 2.5:
                last_print = time.time()
                clear_screen()
                print("="*75)
                print(f"📱  PROBE REQUEST SNIFFER | Kanál: {hopper.current_channel} | Zariadení: {len(probes)}")
                print("="*75)
                print(f"{'MAC ADRESA':<19} {'VÝROBCA':<18} {'RSSI':<9} {'HĽADANÉ SIETE (SSID)'}")
                print("-" * 75)
                for mac, ssids in list(probes.items())[-15:]:
                    v = device_vendor.get(mac, "Neznámy")[:16]
                    r = f"{device_rssi.get(mac, -99)} dBm"
                    ssid_list = ", ".join(list(ssids)[:3])
                    print(f"{mac:<19} {v:<18} {r:<9} {ssid_list}")
                print("-" * 75)
                print("Stlačte Ctrl+C pre návrat do menu...")

    except KeyboardInterrupt:
        print("\n[*] Ukončujem Probe sniffer...")
    finally:
        hopper.stop()
        proc.terminate()

# =============================================================================
# BONUS MODUL 7: STRÁŽNY PES PROTI EVIL TWIN (Rogue AP Watchdog)
# =============================================================================
def run_evil_twin_watchdog(iface):
    print("\n" + "="*70)
    print("🚨  BONUS MODUL 7: STRÁŽNY PES PROTI EVIL TWIN (Rogue AP Watchdog)")
    print("="*70)
    print("Tento nástroj chráni vašu sieť. Ak niekto v okolí spustí falošný klon")
    print("vašej Wi-Fi (napr. s cieľom zachytiť heslá), okamžite spustí poplach.\n")

    my_ssid = input("Zadajte presný názov (SSID) vašej domácej siete: ").strip()
    if not my_ssid:
        print("[!] Nezadaný názov siete.")
        return

    my_bssid = input("Zadajte pravú MAC adresu (BSSID) vášho routera: ").strip().lower()
    if not my_bssid:
        print("[!] Nezadaná BSSID routera.")
        return

    hopper = ChannelHopper(iface)
    hopper.start()

    proc = start_tshark_capture(iface, display_filter="wlan.fc.type_subtype == 8")
    print(f"\n[+] Watchdog aktívny pre sieť '{my_ssid}' [Legitímna MAC: {my_bssid}]")
    print("[*] Sledujem rádiový éter na všetkých kanáloch...\n")

    try:
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) < 6:
                continue

            rssi_str = parts[1]
            src_mac = parts[3].lower()
            bssid = parts[4].lower()
            ssid = parts[5]

            try:
                rssi = int(rssi_str.split(",")[0])
            except Exception:
                rssi = -99

            if ssid.lower() == my_ssid.lower():
                if bssid != my_bssid:
                    print("\a" * 3)
                    print("="*75)
                    print(f"🚨🚨🚨 [KRITICKÝ POPLACH: DETEGOVANÝ EVIL TWIN ATTACK!] 🚨🚨🚨")
                    print(f"  Podvodný AP vysiela váš názov siete: '{ssid}'")
                    print(f"  Falošná MAC (BSSID): {bssid} (RSSI: {rssi} dBm na kanáli {hopper.current_channel})")
                    print(f"  Vaša legitímna MAC:   {my_bssid}")
                    print("  VAROVANIE: Niekto v okolí sa pokúša odchytiť prihlasovacie údaje!")
                    print("="*75 + "\n")
                else:
                    print(f"\r[OK] Legitímny router zachytený ({bssid}) | RSSI: {rssi} dBm | Kanál: {hopper.current_channel}   ", end="")

    except KeyboardInterrupt:
        print("\n[*] Ukončujem Watchdog...")
    finally:
        hopper.stop()
        proc.terminate()

# =============================================================================
# HLAVNÉ MENU
# =============================================================================
def main():
    check_root()

    interfaces = get_wireless_interfaces()
    if not interfaces:
        print("[!] Neboli nájdené žiadne bezdrôtové rozhrania!")
        sys.exit(1)

    default_iface = "wlp0s20u2" if "wlp0s20u2" in interfaces else interfaces[0]

    clear_screen()
    print("="*70)
    print("       📡  MCGYVER WI-FI SUITE (D-Link DWA-137 + Router)  📡")
    print("="*70)
    print(f"Dostupné bezdrôtové rozhrania: {', '.join(interfaces)}")
    iface = input(f"Zvoľte rozhranie pre monitor mód [{default_iface}]: ").strip() or default_iface

    # Kontrola módu
    mode = get_interface_mode(iface)
    if mode != "monitor":
        ans = input(f"Rozhranie {iface} je v režime '{mode}'. Prepnúť teraz do 'monitor' módu? (A/n): ").strip().lower()
        if ans in ["", "a", "ano", "y", "yes"]:
            set_monitor_mode(iface, True)
        else:
            print("[!] Pre beh týchto funkcií je potrebný monitor mode.")
            sys.exit(1)

    while True:
        print("\n" + "="*70)
        print(f"  HLAVNÉ MENU  |  Aktívne rozhranie: {iface} (Monitor Mode)")
        print("="*70)
        print("  [1] 🛰️  Pasívny dronový radar (Remote ID & Drone Sniffer)")
        print("  [3] 🎯  Rádiový zameriavač (Direction Finder / Signal Tracker)")
        print("  [4] 👁️  Neviditeľný pohybový radar (Wi-Fi Human Sensing)")
        print("  [5] 📱  BONUS: Detektor smartfónov (Probe Request Hunter)")
        print("  [7] 🚨  BONUS: Strážny pes proti Evil Twin (Rogue AP Watchdog)")
        print("  [M] 🔄  Prepnúť späť do Managed módu (bežný internet)")
        print("  [0] ❌  Koniec")
        print("="*70)

        choice = input("Zvoľte modul [1, 3, 4, 5, 7, M, 0]: ").strip().upper()

        if choice == "1":
            run_drone_radar(iface)
        elif choice == "3":
            run_direction_finder(iface)
        elif choice == "4":
            run_motion_detector(iface)
        elif choice == "5":
            run_probe_sniffer(iface)
        elif choice == "7":
            run_evil_twin_watchdog(iface)
        elif choice == "M":
            set_monitor_mode(iface, False)
        elif choice == "0":
            ans = input(f"Chcete pred ukončením vrátiť rozhranie {iface} do normálneho režimu (Managed)? (A/n): ").strip().lower()
            if ans in ["", "a", "ano", "y", "yes"]:
                set_monitor_mode(iface, False)
                subprocess.run(["systemctl", "restart", "NetworkManager"], check=False)
            print("[+] Majte pekný deň. Dovidenia!")
            break
        else:
            print("[!] Neplatná voľba.")

if __name__ == "__main__":
    main()
