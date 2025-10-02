import argparse
import os

p = argparse.ArgumentParser()
p.add_argument("--bind-ip", required=False, help="z.B. 0.0.0.0 oder 127.0.0.2")
args = p.parse_args()

if args.bind_ip is not None:
    BIND_IP = args.bind_ip 
else:
    BIND_IP = ""

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
CONFS_DIR = os.path.join(BASE_DIR, "configs")
os.makedirs(CONFS_DIR, exist_ok=True)

PANEL_LAYOUT_FILE = os.path.join(CONFS_DIR, "panel_layout.json")
CONFIG_FILE = os.path.join(CONFS_DIR, "config.json")

STATIC_DIR = "static"
MEDIA_DIR = os.path.join(BASE_DIR, "media")
PLAYLIST_DIR = os.path.join(BASE_DIR, "playlists")
TEMPLATE_DIR = os.path.join(BASE_DIR, "screeny", "templates")
LOG_DIR = os.path.join(BASE_DIR, "logs")

SCREEN_W = 128
SCREEN_H = 128
LINE_NUMS = (0,)
UDP_PORT = 2000
BROADCAST_IP = "255.255.255.255"
