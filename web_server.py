#!/usr/bin/env python3
"""
MC.GYVER RF // LAN Web Server pre iPhone a Desktop
Spúšťa lokálny server prístupný v celej domácej Wi-Fi sieti.
"""

import http.server
import socketserver
import os
import sys
import socket

PORT = 9000
WEB_DIR = "/home/vorkei/.gemini/antigravity/scratch/mcgyver_wifi"

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Pripojenie na neverejnú IP pre zistenie správneho rozhrania (bez odoslania dát)
        s.connect(('192.168.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, format, *args):
        # Farebný výpis požiadaviek z iPhone / desktopu
        client_ip = self.client_address[0]
        user_agent = self.headers.get('User-Agent', '')
        device_type = "📱 iPhone / Mobile" if ("iPhone" in user_agent or "Mobile" in user_agent) else "💻 Desktop / Iné"
        print(f"\033[1;32m[POŽIADAVKA]\033[0m {client_ip} ({device_type}) -> {args[0]}")

class ReusableServer(socketserver.TCPServer):
    allow_reuse_address = True

def main():
    os.chdir(WEB_DIR)
    local_ip = get_local_ip()

    print("\033[1;36m" + "="*70)
    print("🛰️  MC.GYVER TERMINAL // LAN SERVER PRE IPHONE & LOKÁLNU SIEŤ")
    print("="*70 + "\033[0m")
    print(f"📡 Adresa vášho počítača v sieti: \033[1;33m{local_ip}\033[0m")
    print(f"🌐 Port servera:                   \033[1;33m{PORT}\033[0m")
    print("-" * 70)
    print("\033[1;32m👉 OTVORTE TENTO ODKAZ NA IPHONE V SAFARI ALEBO CHROME:\033[0m")
    print(f"\n   \033[1;37;42m  http://{local_ip}:{PORT}  \033[0m\n")
    print("-" * 70)
    print("💡 Tip pre iPhone:")
    print("   1. V Safari ťuknite na ikonu 'Zdieľať' (štvorec so šípkou nahor).")
    print("   2. Zvoľte 'Pridať na plochu' (Add to Home Screen).")
    print("   3. Terminal sa spustí ako plnohodnotná samostatná aplikácia na celú obrazovku (100dvh)!")
    print("-" * 70)
    print("Stlačte Ctrl+C pre zastavenie servera...\n")

    try:
        with ReusableServer(("0.0.0.0", PORT), CustomHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Server zastavený.")
        sys.exit(0)

if __name__ == "__main__":
    main()
