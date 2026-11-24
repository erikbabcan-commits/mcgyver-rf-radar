#!/usr/bin/env python3
"""
EXPRESNÝ DRONOVÝ INTERCEPTOR - 2.4 GHz
Okamžitý záchyt signálov v okruhu 15 metrov.
"""
import sys, os, time, subprocess, threading

DRONE_VENDORS = ["dji", "parrot", "yuneec", "autel", "skydio", "espressif", "tello", "futaba", "frsky"]

def channel_hopper(iface, stop_flag):
    # Rýchle skenovanie najbežnejších kanálov pre drony (1, 6, 11, a ostatné)
    channels = [1, 6, 11, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13]
    while not stop_flag.is_set():
        for ch in channels:
            if stop_flag.is_set(): break
            subprocess.run(["iw", "dev", iface, "set", "channel", str(ch)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.2)

def main():
    iface = "wlp0s20u2"
    print("\033[1;31m" + "="*70)
    print("🚨 EXPRESNÝ DRONOVÝ SKENER AKTÍVNY - ZACHYTÁVAM OKOLIE DO 15 METROV! 🚨")
    print("="*70 + "\033[0m")
    
    stop_flag = threading.Event()
    hopper = threading.Thread(target=channel_hopper, args=(iface, stop_flag), daemon=True)
    hopper.start()

    cmd = [
        "tshark", "-i", iface, "-l", "-n",
        "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "radiotap.dbm_antsignal",
        "-e", "wlan.fc.type_subtype",
        "-e", "wlan.sa",
        "-e", "wlan.ssid",
        "-e", "wlan.sa.oui_resolved"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    seen = {}

    try:
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) < 4: continue
            
            rssi_str = parts[1]
            src_mac = parts[3].lower()
            ssid = parts[4] if len(parts) > 4 else ""
            vendor = parts[5] if len(parts) > 5 else ""

            try:
                rssi = int(rssi_str.split(",")[0])
            except:
                continue

            # Signál z 12 metrov býva silný (zvyčajne silnejší ako -65 dBm)
            is_close = rssi > -65
            is_drone_vendor = any(v in vendor.lower() for v in DRONE_VENDORS)
            is_drone_ssid = any(v in ssid.lower() for v in DRONE_VENDORS)

            if is_drone_vendor or is_drone_ssid or is_close:
                now = time.time()
                # Aktualizuj iba ak je nový alebo sa zmenil signál
                if src_mac not in seen or now - seen[src_mac]['time'] > 2:
                    seen[src_mac] = {'time': now, 'rssi': rssi}
                    tag = "🚨 [DRON PODPIS!]" if (is_drone_vendor or is_drone_ssid) else "📡 [SILNÝ SIGNÁL V BLÍZKOSTI!]"
                    color = "\033[1;31m" if (is_drone_vendor or is_drone_ssid) else "\033[1;33m"
                    print(f"\a{color}{tag} MAC: {src_mac} | RSSI: {rssi} dBm | Vendor: {vendor or 'Neznámy'} | SSID: {ssid or '<skryté>'}\033[0m")

    except KeyboardInterrupt:
        print("\n[*] Zastavené.")
    finally:
        stop_flag.set()
        proc.terminate()

if __name__ == "__main__":
    main()
