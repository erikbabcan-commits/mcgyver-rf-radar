#!/usr/bin/env python3
"""
MC.GYVER MODE SWITCHER - Jednoduchý prepínač režimov pre D-Link (wlp0s20u2)
Použitie:
    sudo ./toggle_mode.py monitor   -> zapne Monitor Mode (pre radar a sniffer)
    sudo ./toggle_mode.py managed   -> vráti do normálu (pre bežný internet)
    sudo ./toggle_mode.py           -> automaticky prepne na opačný stav (toggle)
"""

import sys
import os
import subprocess

IFACE = "wlp0s20u2"

def check_root():
    if os.geteuid() != 0:
        print("\033[1;31m[!] Tento skript vyžaduje práva roota na prepnutie sieťovej karty!\033[0m")
        print(f"    Spustite: sudo python3 {sys.argv[0]} [monitor|managed]")
        sys.exit(1)

def get_current_mode():
    try:
        out = subprocess.check_output(["iw", "dev", IFACE, "info"], text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("type "):
                return line.split()[1]
    except Exception:
        pass
    return "unknown"

def set_monitor():
    print(f"\033[1;36m[*] Prepínam {IFACE} do režimu: MONITOR MODE (Odpočúvanie éteru)...\033[0m")
    # 1. Odpojíme od NetworkManageru, aby mu nevnucoval AP hotspot
    subprocess.run(["nmcli", "device", "disconnect", IFACE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["nmcli", "device", "set", IFACE, "managed", "no"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 2. Vypneme rozhranie, zmeníme typ a zapneme
    subprocess.run(["ip", "link", "set", IFACE, "down"], check=True)
    subprocess.run(["iw", "dev", IFACE, "set", "type", "monitor"], check=True)
    subprocess.run(["ip", "link", "set", IFACE, "up"], check=True)
    # 3. Nastavíme počiatočný kanál 6
    subprocess.run(["iw", "dev", IFACE, "set", "channel", "6"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\033[1;32m[✓] HOTOVO! Rozhranie {IFACE} je teraz v stave: MONITOR MODE (Kanál 6).\033[0m")
    print("    Anténa teraz počúva surové pakety na 2.4 GHz.")

def set_managed():
    print(f"\033[1;36m[*] Vraciam {IFACE} do režimu: MANAGED MODE (Bežný internet)...\033[0m")
    subprocess.run(["ip", "link", "set", IFACE, "down"], check=True)
    subprocess.run(["iw", "dev", IFACE, "set", "type", "managed"], check=True)
    subprocess.run(["ip", "link", "set", IFACE, "up"], check=True)
    subprocess.run(["nmcli", "device", "set", IFACE, "managed", "yes"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "restart", "NetworkManager"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\033[1;32m[✓] HOTOVO! Rozhranie {IFACE} je vrátené do normálu (MANAGED MODE).\033[0m")
    print("    NetworkManager bol obnovený.")

def main():
    check_root()
    curr = get_current_mode()
    print(f"\033[1;33m[i] Aktuálny stav rozhrania {IFACE}: {curr.upper()}\033[0m")

    target = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if target in ["monitor", "mon", "1"]:
        set_monitor()
    elif target in ["managed", "man", "0"]:
        set_managed()
    else:
        # Toggle na opačný mód
        if curr == "monitor":
            set_managed()
        else:
            set_monitor()

    final = get_current_mode()
    print(f"\033[1;37m---> Výsledný režim: {final.upper()}\033[0m")

if __name__ == "__main__":
    main()
