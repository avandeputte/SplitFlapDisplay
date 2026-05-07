import serial
import time
import threading
import json
import os
import logging
import random
import requests
import pytz
import yfinance as yf
from datetime import datetime
from flask import Flask, render_template, request, jsonify

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600
CONFIG_PATH = "/home/gordo/splitflap/settings.json"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

serial_lock = threading.Lock()

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.5)
except Exception as e:
    ser = None
    logging.error(f"Serial failed. Simulation Mode. Reason: {e}")

# --- GLOBAL STATE ---
FLAP_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;q:%'.,/?*roygbpw"
current_indices = [-1] * 45
current_display_string = " " * 45
is_homed = False


# ============================================================
#  SETTINGS
# ============================================================

def load_settings():
    defaults = {
        "offsets":       {str(i): 2832 for i in range(45)},
        "calibrations":  {str(i): 4096 for i in range(45)},
        "tuned_chars":   {str(i): {} for i in range(45)},
        "zip_code":      "02118",
        "timezone":      "US/Eastern",
        "weather_api_key": "",
        "mbta_stop":     "place-bbsta",
        "mbta_route":    "Orange",
        "stocks_list":   "MSFT,GOOG,NVDA",
        "nhl_teams":     "BOS,DAL",
        "yt_channel_id": "UC2GJfspFn6o4liy6GaBFtHA",
        "yt_api_key":    "",
        "yt_video_id":   "",
        "auto_home":     True,
        "countdown_event":   "NEW YEAR",
        "countdown_target":  "2027-01-01T00:00:00",
        "world_clock_zones": "US/Eastern,US/Pacific,Europe/London",
        "crypto_list":   "bitcoin,ethereum,solana",
        "anim_style":    "ltr",
        "anim_speed":    "0.4",
        "anim_text":     "SPLIT  FLAP  DISPLAY",
        "saved_playlists": {},
        "livestream_interval": "25",
        "livestream_comments": "",
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                data = json.load(f)
                defaults.update(data)
                if "tuned_chars" not in defaults:
                    defaults["tuned_chars"] = {str(i): {} for i in range(45)}
                return defaults
        except:
            pass
    return defaults

def save_settings(data):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=4)

settings = load_settings()


# ============================================================
#  SERIAL HELPERS
# ============================================================

def send_raw(cmd):
    if not cmd.endswith('\n'):
        cmd += '\n'
    with serial_lock:
        if ser:
            ser.write(cmd.encode())
            ser.flush()
            time.sleep(0.02)

def sync_hardware_data(mod_id):
    if not ser:
        return False
    with serial_lock:
        ser.reset_input_buffer()
        ser.write(f"m{mod_id:02d}d\n".encode())
        ser.flush()
        start = time.time()
        buffer = ""
        target = f"m{mod_id:02d}d:"
        while time.time() - start < 5.0:
            if ser.in_waiting > 0:
                try:
                    chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    buffer += chunk
                    if target in buffer and '\n' in buffer[buffer.find(target):]:
                        valid_part = buffer[buffer.find(target):].split('\n')[0]
                        data = valid_part.split('d:', 1)[1]
                        parts = data.split(':')
                        if len(parts) >= 2:
                            settings['offsets'][str(mod_id)] = int(parts[0])
                            settings['calibrations'][str(mod_id)] = int(parts[1])
                            settings['tuned_chars'][str(mod_id)] = {}
                            if len(parts) == 3 and parts[2]:
                                for p in parts[2].split(','):
                                    if '=' in p:
                                        idx, val = p.split('=')
                                        settings['tuned_chars'][str(mod_id)][idx] = int(val)
                            save_settings(settings)
                            return True
                except Exception as e:
                    logging.error(f"Parse error: {e}")
            time.sleep(0.05)
    return False


# ============================================================
#  ANIMATION ORDER GENERATORS
# ============================================================

def get_animation_order(style='ltr'):
    """Return a list of the 45 module indices in the requested send order."""
    def m(r, c): return r * 15 + c

    if style == 'rtl':
        return list(range(44, -1, -1))

    elif style == 'center_out':
        order, seen = [], set()
        for d in range(8):
            for r in range(3):
                cols = [7] if d == 0 else [7 - d, 7 + d]
                for c in cols:
                    if 0 <= c < 15:
                        idx = m(r, c)
                        if idx not in seen:
                            seen.add(idx); order.append(idx)
        return order

    elif style == 'outside_in':
        return list(reversed(get_animation_order('center_out')))

    elif style == 'spiral':
        vis = [[False] * 15 for _ in range(3)]
        order = []
        top, bottom, left, right = 0, 2, 0, 14
        while top <= bottom and left <= right:
            for c in range(left, right + 1):
                if not vis[top][c]:
                    vis[top][c] = True; order.append(m(top, c))
            for r in range(top + 1, bottom + 1):
                if not vis[r][right]:
                    vis[r][right] = True; order.append(m(r, right))
            if top < bottom:
                for c in range(right - 1, left - 1, -1):
                    if not vis[bottom][c]:
                        vis[bottom][c] = True; order.append(m(bottom, c))
            if left < right:
                for r in range(bottom - 1, top, -1):
                    if not vis[r][left]:
                        vis[r][left] = True; order.append(m(r, left))
            top += 1; bottom -= 1; left += 1; right -= 1
        return order

    elif style == 'diagonal':
        order, seen = [], set()
        for d in range(17):
            for r in range(3):
                c = d - r
                if 0 <= c < 15:
                    idx = m(r, c)
                    if idx not in seen:
                        seen.add(idx); order.append(idx)
        return order

    elif style == 'anti_diagonal':
        order, seen = [], set()
        for d in range(17):
            for r in range(3):
                c = (14 - d) + r
                if 0 <= c < 15:
                    idx = m(r, c)
                    if idx not in seen:
                        seen.add(idx); order.append(idx)
        return order

    elif style == 'random':
        return random.sample(range(45), 45)

    elif style == 'rain':
        return [m(r, c) for r in range(3) for c in range(15)]

    elif style == 'reverse_rain':
        return [m(r, c) for r in range(2, -1, -1) for c in range(15)]

    elif style == 'columns':
        return [m(r, c) for c in range(15) for r in range(3)]

    elif style == 'columns_rtl':
        return [m(r, c) for c in range(14, -1, -1) for r in range(3)]

    elif style == 'alternating':
        # Row 0 (top) LTR, Row 1 (middle) RTL, Row 2 (bottom) LTR — interleaved column-by-column
        order = []
        for c in range(15):
            order.append(m(0, c))           # top row: left to right
            order.append(m(1, 14 - c))      # middle row: right to left
            order.append(m(2, c))           # bottom row: left to right
        return order

    return list(range(45))  # default ltr


# ============================================================
#  DISPLAY
# ============================================================

COLOR_MAP = {
    '\U0001f7e5': 'r', '\U0001f7e7': 'o', '\U0001f7e8': 'y', '\U0001f7e9': 'g',
    '\U0001f7e6': 'b', '\U0001f7ea': 'p', '\u2b1c': 'w', '\u2b1b': ' ',
}

def send_to_display(text, order=None, raw=False, step_delay_ms=15):
    global current_indices, current_display_string, is_homed
    if not text:
        return 0

    # For normal text: uppercase first (emojis are unaffected by upper()),
    # then replace emojis with color codes. Animation pages pass raw=True to
    # skip uppercasing so their color codes (r o y g b p w) stay lowercase.
    if not raw:
        clean_text = text.upper()
    else:
        clean_text = text
    for emoji, char in COLOR_MAP.items():
        clean_text = clean_text.replace(emoji, char)
    # The physical " flap is addressed as 'q' in the firmware character map
    clean_text = clean_text.replace('"', 'q')
    clean_text = clean_text.ljust(45)[:45]
    logging.info(f"DISPLAY: {clean_text}")

    if order is None:
        order = list(range(45))

    max_dist = 0
    with serial_lock:
        for i in order:
            if i >= len(clean_text):
                continue
            char = clean_text[i]
            if ser:
                ser.write(f"m{i:02d}-{char}\n".encode())
                ser.flush()
                time.sleep(step_delay_ms / 1000.0)

            target_idx = FLAP_CHARS.find(char)
            if target_idx == -1:
                target_idx = 0
            dist = 128 if current_indices[i] == -1 else (target_idx - current_indices[i]) % 64
            if dist > max_dist:
                max_dist = dist
            current_indices[i] = target_idx

    current_display_string = clean_text
    is_homed = True
    return max_dist


# ============================================================
#  ANIMATION CONTENT GENERATORS
# ============================================================

def generate_rainbow_pages():
    """7 pages cycling the colour tiles across the board."""
    colors = 'roygbpw'
    return [''.join(colors[(c + off) % 7] for r in range(3) for c in range(15))
            for off in range(7)]

def generate_sweep_pages():
    """Colour band sweeping left→right then right→left."""
    colors = 'roygbpw'
    pages = []
    for i in range(1, 16):
        col = colors[i % 7]
        pages.append(''.join(col if c < i else ' ' for r in range(3) for c in range(15)))
    for i in range(14, 0, -1):
        col = colors[(i + 3) % 7]
        pages.append(''.join(col if c < i else ' ' for r in range(3) for c in range(15)))
    return pages

def generate_twinkle_pages(n=12):
    """Sparse random colour dots."""
    colors = 'roygbpw   '  # extra spaces for sparsity
    return [''.join(random.choice(colors) for _ in range(45)) for _ in range(n)]

def generate_checker_pages():
    """Alternating two-colour checkerboard that swaps through several palettes."""
    pages = []
    pairs = [('r', 'b'), ('o', 'p'), ('y', 'g'), ('r', 'w'), ('g', 'b')]
    for a, b in pairs:
        p1 = ''.join(a if (r + c) % 2 == 0 else b for r in range(3) for c in range(15))
        p2 = ''.join(b if (r + c) % 2 == 0 else a for r in range(3) for c in range(15))
        pages += [p1, p2]
    return pages

def run_matrix_animation():
    """
    Matrix cascade: 3 frames of random chars (each using a different
    send order), then the target text revealed in the configured style.
    """
    global last_sent_page, loop_delay
    target = settings.get('anim_text', 'SPLIT  FLAP  DISPLAY').upper().ljust(45)[:45]
    speed  = max(0.1, float(settings.get('anim_speed', '0.4')))
    style  = settings.get('anim_style', 'ltr')
    chars  = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&?%*-+'

    frame_plan = [
        ('random',  lambda: ''.join(random.choice(chars) for _ in range(45))),
        ('rain',    lambda: ''.join(random.choice(chars) for _ in range(45))),
        ('spiral',  lambda: ''.join(random.choice(chars) for _ in range(45))),
        (style,     lambda: target),
    ]

    for order_name, content_fn in frame_plan:
        if stop_event.is_set():
            return
        order    = get_animation_order(order_name)
        page     = content_fn()
        max_dist = send_to_display(page, order, raw=True)
        last_sent_page = page

        rotation_time = max_dist * (4.0 / 64.0)
        deadline = time.time() + max(speed, rotation_time)
        while time.time() < deadline:
            if stop_event.is_set():
                return
            time.sleep(0.05)

    # Hold final text for loop_delay
    deadline = time.time() + float(loop_delay)
    while time.time() < deadline:
        if stop_event.is_set():
            return
        time.sleep(0.1)


# ============================================================
#  DEMO MODE
# ============================================================

def run_demo():
    """
    Scripted showcase sequence for filming.  Loops until stopped.
    The first iteration includes an 8-second lead-in delay so the
    operator can get behind the camera.
    """
    global last_sent_page

    # ── helpers ────────────────────────────────────────────
    def wait(secs):
        """Sleep in small increments, bailing if stop_event fires."""
        end = time.time() + secs
        while time.time() < end:
            if stop_event.is_set():
                return False
            time.sleep(0.05)
        return True

    def show(text, dur=4, style='ltr', raw_flag=False):
        """Send one page, wait for rotation + display time."""
        order = get_animation_order(style)
        padded = text.ljust(45)[:45]
        md = send_to_display(padded, order, raw=raw_flag)
        last_sent_page = padded
        rot = md * (4.0 / 64.0)
        if not wait(max(rot, 0.3)):
            return False
        return wait(dur)

    def play_pages(pages, spd=0.4, style='ltr'):
        """Play a list of raw colour pages."""
        order = get_animation_order(style)
        for p in pages:
            if stop_event.is_set():
                return False
            md = send_to_display(p, order, raw=True)
            last_sent_page = p
            rot = md * (4.0 / 64.0)
            if not wait(max(rot, spd)):
                return False
        return True

    def matrix_burst(reveal_text, reveal_style='center_out'):
        """Three random-char frames then a clean reveal."""
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&?%*'
        for sty in ('random', 'rain', 'spiral'):
            if stop_event.is_set():
                return False
            noise = ''.join(random.choice(chars) for _ in range(45))
            order = get_animation_order(sty)
            md = send_to_display(noise, order, raw=True)
            last_sent_page = noise
            if not wait(max(md * (4.0 / 64.0), 0.6)):
                return False
        return show(reveal_text, 5, reveal_style)

    # ── initial lead-in ───────────────────────────────────
    if not wait(8):
        return

    # ── main loop ─────────────────────────────────────────
    first_loop = True
    while not stop_event.is_set():

        # 1 ─ Title reveal
        if not show('               '
                     '  SPLIT  FLAP  '
                     '               ', 1, 'ltr'):
            return
        if not show('   SPLIT  FLAP '
                     '    DISPLAY    '
                     '               ', 5, 'center_out'):
            return

        # 2 ─ Rainbow wave
        if not play_pages(generate_rainbow_pages() * 3, 0.5, 'columns'):
            return

        # 3 ─ Feature text
        if not show('  HANDMADE WITH'
                     '   45 MODULES  '
                     ' EACH ONE UNIQUE', 5, 'spiral'):
            return

        # 4 ─ Colour sweep
        if not play_pages(generate_sweep_pages(), 0.2):
            return

        # 5 ─ Full colour gradient
        gradient = 'rrrrroooooyyyyygggggbbbbbpppppwwwwwrrrrrooooo'
        if not show(gradient, 4, 'rain', raw_flag=True):
            return

        # 6 ─ Dimensions text
        if not show(' 3 ROWS  OF 15 '
                     '  CHARACTERS   '
                     '  = 45 TOTAL   ', 5, 'diagonal'):
            return

        # 7 ─ Checker animation
        if not play_pages(generate_checker_pages() * 2, 0.6, 'center_out'):
            return

        # 8 ─ Data apps callout
        if not show('   REAL  TIME  '
                     '   DATA  APPS  '
                     '   BUILT  IN   ', 5, 'rtl'):
            return

        # 9 ─ Matrix cascade
        if not matrix_burst(
                '   POWERED BY  '
                '   ARDUINO  &  '
                '  RASPBERRY PI ', 'outside_in'):
            return

        # 10 ─ Twinkle sparkle
        if not play_pages(generate_twinkle_pages(10), 0.45, 'random'):
            return

        # 11 ─ Alphabet / charset flex
        if not show(' ABCDEFGHIJKLMN'
                     'OPQRSTUVWXYZ   '
                     '0123456789!@#$&', 5, 'columns'):
            return

        # 12 ─ Second matrix cascade
        if not matrix_burst(
                '               '
                '  SUBSCRIBE!   '
                '               ', 'center_out'):
            return

        # 13 ─ Closing
        if not show('               '
                     ' THANKS  FOR   '
                     '  WATCHING!    ', 6, 'center_out'):
            return

        # Brief pause before looping
        if not wait(4):
            return

        first_loop = False


# ============================================================
#  APP DATA FETCHERS
# ============================================================

def format_lines(l1, l2, l3):
    return l1.center(15)[:15] + l2.center(15)[:15] + l3.center(15)[:15]

def fetch_weather_data():
    api_key  = settings.get("weather_api_key", "").strip()
    zip_code = settings.get("zip_code", "02118").strip()
    if not api_key:
        return None
    try:
        url = (f"http://api.openweathermap.org/data/2.5/weather"
               f"?zip={zip_code},us&appid={api_key}&units=imperial")
        res = requests.get(url, timeout=5).json()
        return {
            'city':  res['name'].upper(),
            'temp':  round(res['main']['temp']),
            'feels': round(res['main']['feels_like']),
            'desc':  res['weather'][0]['main'].upper(),
            'high':  round(res['main']['temp_max']),
            'low':   round(res['main']['temp_min']),
        }
    except:
        return None

def fetch_metro():
    stop  = settings.get('mbta_stop', 'place-bbsta')
    route = settings.get('mbta_route', 'Orange')
    url = (f"https://api-v3.mbta.com/predictions"
           f"?filter[stop]={stop}&filter[route]={route}&page[limit]=20&sort=departure_time")
    try:
        predictions = requests.get(url, timeout=5).json().get('data', [])
        dirs = {0: [], 1: []}
        for p in predictions:
            dt = p['attributes']['departure_time']
            if not dt:
                continue
            mins = int((datetime.fromisoformat(dt).astimezone(pytz.utc)
                        - datetime.now(pytz.utc)).total_seconds() / 60)
            if mins < 0:
                continue
            d = p['attributes']['direction_id']
            if d in dirs and len(dirs[d]) < 2:
                dirs[d].append(str(mins))

        def fmt(name, times):
            if not times: return f"{name} ---".ljust(15)
            return f"{name} {','.join(times)}M"[:15].ljust(15)

        # Use orange colour tiles for the header
        header = ('\U0001f7e7\U0001f7e7' + route.upper()[:9] + '\U0001f7e7\U0001f7e7').center(15)
        return [header + fmt("OAK GRV", dirs[1]) + fmt("FRST HLS", dirs[0])]
    except:
        return [format_lines("METRO ERROR", "", "")]

def fetch_stocks():
    tickers = [t.strip() for t in settings.get('stocks_list', 'MSFT,GOOG,NVDA').split(',') if t.strip()]
    pages = []
    for chunk in [tickers[i:i+3] for i in range(0, len(tickers), 3)]:
        pl = ["               "] * 3
        cl = ["               "] * 3
        for idx, sym in enumerate(chunk):
            try:
                si   = yf.Ticker(sym).fast_info
                prc  = si.last_price
                prev = si.previous_close
                pct  = ((prc - prev) / prev) * 100
                sign = "+" if pct >= 0 else ""
                pl[idx] = f"{sym[:5]:<5} ${prc:<7.2f}"[:15].ljust(15)
                cl[idx] = f"{sym[:5]:<5} {sign}{pct:.2f}%"[:15].ljust(15)
            except:
                pl[idx] = cl[idx] = f"{sym[:5]:<5} ERR".ljust(15)
        pages += [pl[0]+pl[1]+pl[2], cl[0]+cl[1]+cl[2]]
    return pages or [format_lines("NO STOCKS", "CONFIGURED", "")]

def fetch_sports():
    teams = [t.strip() for t in settings.get('nhl_teams', 'BOS,DAL').split(',') if t.strip()]
    pages = []
    try:
        games = requests.get("https://api-web.nhle.com/v1/score/now", timeout=5).json().get('games', [])
        for g in games:
            away = g['awayTeam']['abbrev']
            home = g['homeTeam']['abbrev']
            if away in teams or home in teams:
                score = f"{away} {g['awayTeam'].get('score',0)} {home} {g['homeTeam'].get('score',0)}"
                state = g['gameState']
                if state in ('F', 'FINAL'):
                    clock = "FINAL"
                elif state in ('LIVE', 'CRIT'):
                    clock = f"P{g['period']} {g['clock']['timeRemaining']}"
                else:
                    clock = "SCHEDULED"
                pages.append(format_lines("NHL SCORE", score, clock))
        return pages or [format_lines("NHL SCORES", "NO GAMES", "TODAY")]
    except:
        return [format_lines("SPORTS ERR", "", "")]

def fetch_youtube_data():
    cid = settings.get("yt_channel_id", "").strip()
    for url in [
        f"https://mixerno.space/api/youtube-channel-counter/user/{cid}",
        f"https://axern.space/api/get?platform=youtube&type=channel&id={cid}",
    ]:
        try:
            r    = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
            name = (r.get('user', [{}])[0].get('count') or
                    r.get('snippet', {}).get('title', '')).upper()
            subs = (r.get('counts', [{}])[0].get('count') or
                    r.get('statistics', {}).get('subscriberCount', '?'))
            if name:
                return {'name': name, 'subs': subs}
        except:
            pass
    return None

def fetch_youtube_comments():
    api_key  = settings.get("yt_api_key", "").strip()
    video_id = settings.get("yt_video_id", "").strip()
    if not api_key or not video_id:
        return [format_lines("YT COMMENTS", "MISSING API KEY", "OR VIDEO ID")]
    url = (f"https://www.googleapis.com/youtube/v3/commentThreads"
           f"?part=snippet&videoId={video_id}&maxResults=5&order=time&textFormat=plainText&key={api_key}")
    try:
        items = requests.get(url, timeout=5).json().get('items', [])
        if not items:
            return [format_lines("YT COMMENTS", "NO COMMENTS", "FOUND")]
        pages = []
        for item in items:
            sn     = item['snippet']['topLevelComment']['snippet']
            author = ''.join(c for c in sn['authorDisplayName'].upper() if c in FLAP_CHARS)
            text   = sn['textDisplay'].upper().replace('\n', ' ')
            pages.append(author[:15].center(15) + text[0:15].ljust(15) + text[15:30].ljust(15))
        return pages or [format_lines("YT COMMENTS", "FETCH ERROR", "")]
    except:
        return [format_lines("YT COMMENTS", "API ERROR", "")]

def fetch_youtube_viewers():
    """Fetch current concurrent viewer count for a YouTube live stream.
    Returns an int viewer count, or None if unavailable / not live."""
    api_key  = settings.get("yt_api_key", "").strip()
    video_id = settings.get("yt_video_id", "").strip()
    if not api_key or not video_id:
        return None
    url = (f"https://www.googleapis.com/youtube/v3/videos"
           f"?part=liveStreamingDetails&id={video_id}&key={api_key}")
    try:
        data  = requests.get(url, timeout=5).json()
        items = data.get('items', [])
        if not items:
            return None
        details = items[0].get('liveStreamingDetails', {}) or {}
        v = details.get('concurrentViewers')
        return int(v) if v is not None else None
    except Exception as e:
        logging.error(f"YT viewers fetch error: {e}")
        return None


def parse_livestream_comments():
    """Parse user-entered comment blocks into 45-char display pages.
    Blocks are separated by blank lines; within a block each newline
    is one row (up to 3 rows of 15 chars, auto-centered)."""
    raw = settings.get('livestream_comments', '').strip()
    if not raw:
        return []
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    blocks = [b for b in raw.split('\n\n') if b.strip()]
    pages = []
    for block in blocks:
        lines = [l.strip() for l in block.split('\n')]
        lines = [l for l in lines if l]  # drop empty lines inside block
        while len(lines) < 3:
            lines.append('')
        lines = lines[:3]
        page = ''.join(l[:15].center(15)[:15] for l in lines)
        pages.append(page)
    return pages


def build_livestream_pages():
    """Construct the rotating page sequence for livestream mode.
    Each page is a dict with its own transition style for visual variety."""
    pages = []
    tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    dt = datetime.now(tz)
    time_str = dt.strftime("%I:%M %p").lstrip("0")


    # ── 3. YouTube subs (time on top row, as requested) ────────────
    yt = app_caches.get('youtube')
    if yt:
        pages.append({
            'text':  format_lines(
                time_str,
                yt['name'][:15],
                f"{yt['subs']} SUBS",
            ),
            'style': 'ltr',
        })

    # ── 4. Concurrent viewers (bonus) ──────────────────────────────
    viewers = app_caches.get('livestream_viewers')
    if viewers is not None:
        pages.append({
            'text':  format_lines(
                'WATCHING NOW',
                f"{viewers:,}",
                'LIVE VIEWERS',
            ),
            'style': 'diagonal',
        })

    # ── 5. User-defined comment pages (variable count) ─────────────
    comment_pages = parse_livestream_comments()
    varied_styles = ['outside_in', 'spiral', 'anti_diagonal', 'rtl', 'rain', 'center_out']
    for i, cp in enumerate(comment_pages):
        pages.append({
            'text':  cp,
            'style': varied_styles[i % len(varied_styles)],
        })


    return pages


# ── New apps ──────────────────────────────────────────────────

def fetch_countdown():
    event      = settings.get('countdown_event', 'NEW YEAR').upper()[:15]
    target_str = settings.get('countdown_target', '2027-01-01T00:00:00')
    tz         = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    try:
        try:
            target = datetime.fromisoformat(target_str)
        except ValueError:
            target = datetime.strptime(target_str[:16], "%Y-%m-%dT%H:%M")
        if target.tzinfo is None:
            target = tz.localize(target)
        diff = target - datetime.now(pytz.utc).astimezone(tz)
        if diff.total_seconds() <= 0:
            return [format_lines(event, "TIME IS UP!", "")]
        total = int(diff.total_seconds())
        d  = total // 86400
        h  = (total % 86400) // 3600
        mn = (total % 3600) // 60
        s  = total % 60
        return [format_lines(event, f"{d}D {h:02d}H", f"{mn:02d}M {s:02d}S")]
    except Exception as e:
        logging.error(f"Countdown error: {e}")
        return [format_lines("COUNTDOWN", "CONFIG ERROR", "")]

def fetch_world_clock():
    zones_str = settings.get('world_clock_zones', 'US/Eastern,US/Pacific,Europe/London')
    zones = [z.strip() for z in zones_str.split(',') if z.strip()][:3]
    while len(zones) < 3:
        zones.append('UTC')
    LABELS = {
        'US/Eastern': 'EST', 'US/Pacific': 'PST', 'US/Central': 'CST',
        'US/Mountain': 'MST', 'Europe/London': 'LON', 'Europe/Paris': 'PAR',
        'Europe/Berlin': 'BER', 'Asia/Tokyo': 'TYO', 'Asia/Singapore': 'SIN',
        'Asia/Dubai': 'DXB', 'Australia/Sydney': 'SYD', 'UTC': 'UTC',
        'America/New_York': 'NYC', 'America/Los_Angeles': 'LAX',
        'America/Chicago': 'CHI', 'America/Denver': 'DEN',
    }
    lines = []
    for zone in zones:
        try:
            now   = datetime.now(pytz.timezone(zone))
            tstr  = now.strftime("%I:%M%p").lstrip("0")
            label = LABELS.get(zone, zone.split('/')[-1][:4].upper())
            lines.append(f"{label:<4} {tstr}"[:15].ljust(15))
        except:
            lines.append("ERR             "[:15])
    return [lines[0] + lines[1] + lines[2]]

def fetch_crypto():
    coins = [c.strip().lower() for c in
             settings.get('crypto_list', 'bitcoin,ethereum,solana').split(',') if c.strip()][:6]
    url = (f"https://api.coingecko.com/api/v3/simple/price"
           f"?ids={','.join(coins)}&vs_currencies=usd&include_24hr_change=true")
    try:
        data  = requests.get(url, timeout=8).json()
        pages = []
        for chunk in [coins[i:i+3] for i in range(0, len(coins), 3)]:
            pl = ["               "] * 3
            cl = ["               "] * 3
            for idx, coin in enumerate(chunk):
                if coin not in data:
                    pl[idx] = cl[idx] = f"{coin[:4].upper():4} N/A".ljust(15)
                    continue
                usd   = data[coin].get('usd', 0)
                chg   = data[coin].get('usd_24h_change', 0) or 0
                short = coin[:4].upper()
                sign  = '+' if chg >= 0 else ''
                if usd >= 10000:
                    pstr = f"{short} ${usd:,.0f}"
                elif usd >= 1:
                    pstr = f"{short} ${usd:,.2f}"
                else:
                    pstr = f"{short} ${usd:.4f}"
                pl[idx] = pstr[:15].ljust(15)
                cl[idx] = f"{short} {sign}{chg:.1f}%"[:15].ljust(15)
            pages += [pl[0]+pl[1]+pl[2], cl[0]+cl[1]+cl[2]]
        return pages or [format_lines("CRYPTO", "NO DATA", "")]
    except Exception as e:
        logging.error(f"Crypto fetch error: {e}")
        return [format_lines("CRYPTO ERR", "CHECK CONN", "")]

def fetch_iss():
    try:
        pos  = requests.get("http://api.open-notify.org/iss-now.json", timeout=5).json()['iss_position']
        lat  = float(pos['latitude'])
        lon  = float(pos['longitude'])
        ld   = 'N' if lat >= 0 else 'S'
        lnd  = 'E' if lon >= 0 else 'W'
        try:
            crew = len(requests.get("http://api.open-notify.org/astros.json", timeout=3).json()['people'])
            hdr  = f"ISS CREW:{crew}"
        except:
            hdr = "ISS TRACKER"
        l2 = f"LAT {abs(lat):6.2f}{ld}".center(15)
        l3 = f"LON {abs(lon):7.2f}{lnd}".center(15)
        return [hdr.center(15) + l2 + l3]
    except Exception as e:
        logging.error(f"ISS fetch error: {e}")
        return [format_lines("ISS ERR", "CHECK CONN", "")]


# ============================================================
#  PLAYLIST LOOP
# ============================================================

current_playlist = []
loop_delay  = 5
stop_event  = threading.Event()
last_sent_page = None
active_app  = None

last_fetches = {
    'weather': 0, 'metro': 0, 'sports': 0, 'stocks': 0,
    'youtube': 0, 'yt_comments': 0, 'crypto': 0, 'iss': 0,
    'livestream_viewers': 0,
}
app_caches = {
    'weather': None, 'metro': [], 'sports': [], 'stocks': [],
    'youtube': None, 'yt_comments': [], 'crypto': [], 'iss': [],
    'livestream_viewers': None,
}


def playlist_loop():
    global current_playlist, loop_delay, last_sent_page, active_app, last_fetches, app_caches

    while True:
        now = time.time()
        display_pages = []
        active_order  = None   # custom module send order for this cycle

        # ── Demo mode (self-contained loop) ──────────────
        if active_app == 'demo':
            run_demo()
            if stop_event.is_set():
                stop_event.clear()
            continue

        # ── Static / real-time apps ──────────────────────────
        if active_app is None:
            display_pages = current_playlist

        elif active_app == 'time':
            tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
            display_pages = [format_lines("", datetime.now(tz).strftime("%I:%M %p").lstrip("0").center(15), "")]

        elif active_app == 'date':
            tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
            dt = datetime.now(tz)
            display_pages = [format_lines(
                dt.strftime("%I:%M %p").lstrip("0").center(15),
                dt.strftime("%B %d").upper().center(15),
                dt.strftime("%A").upper().center(15),
            )]

        elif active_app == 'countdown':
            display_pages = fetch_countdown()

        elif active_app == 'world_clock':
            display_pages = fetch_world_clock()

        # ── Cached / API apps ────────────────────────────────
        elif active_app == 'weather':
            if now - last_fetches['weather'] > 300:
                app_caches['weather'] = fetch_weather_data()
                last_fetches['weather'] = now
            w = app_caches['weather']
            now_t = datetime.now(pytz.timezone(settings.get('timezone', 'US/Eastern'))).strftime("%I:%M%p").lstrip("0")
            if not w:
                display_pages = [format_lines("NO WEATHER DATA", now_t.center(15), "CHECK API KEY")]
            else:
                mcl = 14 - len(now_t)
                l1  = f"{w['city'][:mcl]} {now_t}".center(15)
                pfx = f"{w['temp']}F ({w['feels']}F) "
                l2  = (pfx + w['desc'][:15-len(pfx)]).center(15)
                l3  = f"H:{w['high']}F L:{w['low']}F".center(15)
                display_pages = [format_lines(l1, l2, l3)]

        elif active_app == 'dashboard':
            tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
            dt = datetime.now(tz)
            time_page = format_lines(dt.strftime("%A").upper(),
                                     dt.strftime("%b %d %Y").upper(),
                                     dt.strftime("%I:%M %p").upper())
            if now - last_fetches['weather'] > 300:
                app_caches['weather'] = fetch_weather_data()
                last_fetches['weather'] = now
            w = app_caches['weather']
            now_t = dt.strftime("%I:%M%p").lstrip("0")
            if not w:
                wp = format_lines("NO WEATHER DATA", now_t.center(15), "CHECK API KEY")
            else:
                mcl = 14 - len(now_t)
                l1  = f"{w['city'][:mcl]} {now_t}".center(15)
                pfx = f"{w['temp']}F ({w['feels']}F) "
                l2  = (pfx + w['desc'][:15-len(pfx)]).center(15)
                l3  = f"H:{w['high']}F L:{w['low']}F".center(15)
                wp  = format_lines(l1, l2, l3)
            display_pages = [time_page, wp]

        elif active_app == 'youtube':
            if now - last_fetches['youtube'] > 30:
                app_caches['youtube'] = fetch_youtube_data()
                last_fetches['youtube'] = now
            yt = app_caches['youtube']
            display_pages = ([format_lines("YOUTUBE", yt['name'][:15].center(15),
                                           f"{yt['subs']} SUBS".center(15))]
                             if yt else [format_lines("YOUTUBE", "FETCH ERROR", "CHECK API")])

        elif active_app == 'yt_comments':
            if now - last_fetches['yt_comments'] > 60:
                app_caches['yt_comments'] = fetch_youtube_comments()
                last_fetches['yt_comments'] = now
            display_pages = app_caches.get('yt_comments', [format_lines("LOADING", "COMMENTS...", "")])

        elif active_app == 'metro':
            if now - last_fetches['metro'] > 30:
                app_caches['metro'] = fetch_metro()
                last_fetches['metro'] = now
            display_pages = app_caches['metro']

        elif active_app == 'stocks':
            if now - last_fetches['stocks'] > 60:
                app_caches['stocks'] = fetch_stocks()
                last_fetches['stocks'] = now
            display_pages = app_caches['stocks']

        elif active_app == 'sports':
            if now - last_fetches['sports'] > 60:
                app_caches['sports'] = fetch_sports()
                last_fetches['sports'] = now
            display_pages = app_caches['sports']

        elif active_app == 'crypto':
            if now - last_fetches['crypto'] > 60:
                app_caches['crypto'] = fetch_crypto()
                last_fetches['crypto'] = now
            display_pages = app_caches.get('crypto', [format_lines("LOADING", "CRYPTO...", "")])

        elif active_app == 'iss':
            if now - last_fetches['iss'] > 5:
                app_caches['iss'] = fetch_iss()
                last_fetches['iss'] = now
            display_pages = app_caches.get('iss', [format_lines("LOADING", "ISS...", "")])

        elif active_app == 'livestream':
            # Refresh YouTube subs every 60s
            if now - last_fetches.get('youtube', 0) > 60:
                app_caches['youtube'] = fetch_youtube_data()
                last_fetches['youtube'] = now
            # Refresh concurrent viewers every 30s
            if now - last_fetches.get('livestream_viewers', 0) > 30:
                app_caches['livestream_viewers'] = fetch_youtube_viewers()
                last_fetches['livestream_viewers'] = now
            display_pages = build_livestream_pages()

        # ── Animation apps ───────────────────────────────────
        elif active_app == 'anim_rainbow':
            display_pages = generate_rainbow_pages()
            active_order  = get_animation_order(settings.get('anim_style', 'ltr'))

        elif active_app == 'anim_sweep':
            display_pages = generate_sweep_pages()
            active_order  = get_animation_order(settings.get('anim_style', 'ltr'))

        elif active_app == 'anim_twinkle':
            display_pages = generate_twinkle_pages()
            active_order  = get_animation_order('random')

        elif active_app == 'anim_checker':
            display_pages = generate_checker_pages()
            active_order  = get_animation_order(settings.get('anim_style', 'ltr'))

        elif active_app == 'anim_matrix':
            # Runs its own multi-order frame sequence; loop back to top when done
            run_matrix_animation()
            if stop_event.is_set():
                stop_event.clear()
            continue

        else:
            display_pages = current_playlist

        if not display_pages:
            time.sleep(1)
            continue

        # Effective per-page delay
        if active_app and active_app.startswith('anim_'):
            eff_delay = max(0.1, float(settings.get('anim_speed', '0.4')))
        elif active_app in ('countdown', 'world_clock', 'time', 'date'):
            eff_delay = 1.0
        else:
            eff_delay = float(loop_delay)

        is_anim = active_app is not None and active_app.startswith('anim_')

        for page in display_pages:
            if stop_event.is_set():
                break

            # Resolve per-page settings — rich playlist objects vs. plain strings
            if isinstance(page, dict):
                page_text  = page.get('text', '')
                page_delay = float(page.get('delay', eff_delay))
                page_order = get_animation_order(page.get('style', 'ltr'))
                page_speed = int(page.get('speed', 15))
            else:
                page_text  = page
                page_delay = eff_delay
                page_order = active_order
                page_speed = 15

            max_dist = 0
            # Animations always resend each frame; other apps skip unchanged pages
            if is_anim or page_text != last_sent_page:
                max_dist = send_to_display(page_text, page_order, raw=is_anim, step_delay_ms=page_speed)
                last_sent_page = page_text

            rotation_time = max_dist * (4.0 / 64.0)
            for _ in range(int(rotation_time * 10)):
                if stop_event.is_set(): break
                time.sleep(0.1)

            for _ in range(int(page_delay * 10)):
                if stop_event.is_set(): break
                time.sleep(0.1)

        if stop_event.is_set():
            stop_event.clear()


threading.Thread(target=playlist_loop, daemon=True).start()


# ============================================================
#  FLASK ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/current_state')
def current_state():
    return jsonify(is_homed=is_homed, state=current_display_string, active_app=active_app)

@app.route('/settings', methods=['GET', 'POST'])
def handle_settings():
    global settings, is_homed, current_indices, current_display_string
    if request.method == 'POST':
        data   = request.json
        action = data.get('action')
        mod_id = str(data.get('id', '0'))

        if action == 'save_global':
            keys = [
                'zip_code', 'weather_api_key', 'timezone',
                'mbta_stop', 'mbta_route', 'stocks_list', 'nhl_teams',
                'yt_channel_id', 'yt_api_key', 'yt_video_id',
                'countdown_event', 'countdown_target', 'world_clock_zones',
                'crypto_list', 'anim_style', 'anim_speed', 'anim_text',
                'livestream_interval', 'livestream_comments',
            ]
            settings.update({k: data[k] for k in keys if k in data})
            save_settings(settings)
            return jsonify(status="Saved")

        if action == 'adjust':
            delta      = int(data.get('delta', 0))
            new_offset = int(settings['offsets'].get(mod_id, 2832)) + delta
            settings['offsets'][mod_id] = new_offset
            save_settings(settings)
            send_raw(f"m{int(mod_id):02d}o{new_offset}")
            return jsonify(new_offset=new_offset)

        if action == 'home_one':
            send_raw(f"m{int(mod_id):02d}h")
            current_indices[int(mod_id)] = 0
            sl = list(current_display_string.ljust(45))
            sl[int(mod_id)] = ' '
            current_display_string = "".join(sl)
            return jsonify(status="Homing")

        if action == 'calibrate':
            with serial_lock:
                if ser:
                    ser.reset_input_buffer()
                    ser.write(f"m{int(mod_id):02d}c\n".encode())
                    ser.flush()
                    start_wait = time.time()
                    buffer = ""
                    target = f"m{int(mod_id):02d}:"
                    while (time.time() - start_wait) < 45.0:
                        if ser.in_waiting > 0:
                            chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                            buffer += chunk
                            if target in buffer and '\n' in buffer[buffer.find(target):]:
                                valid_part = buffer[buffer.find(target):].split('\n')[0]
                                try:
                                    val = int(valid_part.split(target)[1])
                                    settings['calibrations'][mod_id] = val
                                    save_settings(settings)
                                    ser.write(f"m{int(mod_id):02d}t{val}\n".encode())
                                    ser.flush()
                                    return jsonify(status="success", steps=val)
                                except:
                                    pass
                        time.sleep(0.1)
                    return jsonify(status="error", message="Timeout"), 500
    return jsonify(settings)

@app.route('/custom_tune', methods=['POST'])
def custom_tune():
    global current_indices, current_display_string
    data   = request.json
    action = data.get('action')
    mod_id = int(data.get('id', 0))

    if action == 'goto':
        step = int(data.get('step', 0))
        idx  = int(data.get('index', 0))
        send_raw(f"m{mod_id:02d}g{step}")
        if 0 <= idx < len(FLAP_CHARS):
            current_indices[mod_id] = idx
            sl = list(current_display_string.ljust(45))
            sl[mod_id] = FLAP_CHARS[idx]
            current_display_string = "".join(sl)

    elif action == 'save':
        idx  = int(data.get('index', 0))
        step = int(data.get('step', 0))
        send_raw(f"m{mod_id:02d}w{idx}:{step}")
        settings['tuned_chars'][str(mod_id)][str(idx)] = step
        save_settings(settings)

    elif action == 'erase':
        idx = str(data.get('index', ''))
        if idx:
            send_raw(f"m{mod_id:02d}w{idx}:65535")
            settings['tuned_chars'][str(mod_id)].pop(idx, None)
        else:
            send_raw(f"m{mod_id:02d}e")
            settings['tuned_chars'][str(mod_id)] = {}
        save_settings(settings)

    return jsonify(status="Success")

@app.route('/sync_module', methods=['POST'])
def sync_module():
    mod_id  = int(request.json.get('id', 0))
    success = sync_hardware_data(mod_id)
    return jsonify(status="success" if success else "failed", settings=settings)

@app.route('/sync_all', methods=['POST'])
def sync_all():
    for i in range(45):
        sync_hardware_data(i)
    return jsonify(status="success", settings=settings)

@app.route('/assign_id', methods=['POST'])
def assign_id():
    send_raw(f"m**i{int(request.json.get('id', 0)):02d}")
    return jsonify(status="ID Assigned")

@app.route('/toggle_autohome', methods=['POST'])
def toggle_autohome():
    global settings
    enabled = request.json.get('enabled', True)
    settings['auto_home'] = enabled
    save_settings(settings)
    send_raw(f"m**a{1 if enabled else 0}")
    return jsonify(status="Auto-home updated")

@app.route('/update_playlist', methods=['POST'])
def update_playlist():
    global current_playlist, loop_delay, last_sent_page, active_app
    data             = request.json
    current_playlist = data.get('pages', [])
    loop_delay       = data.get('delay', 5)
    last_sent_page   = None
    active_app       = None
    stop_event.set()
    return jsonify(status="success")

@app.route('/run_app', methods=['POST'])
def run_app():
    global active_app, last_fetches, loop_delay
    active_app   = request.json.get('app')
    last_fetches = {k: 0 for k in last_fetches}

    if active_app == 'stocks':
        loop_delay = 10
    elif active_app in ('countdown', 'world_clock'):
        loop_delay = 1
    elif active_app == 'livestream':
        try:
            loop_delay = max(5, int(float(settings.get('livestream_interval', 25))))
        except (TypeError, ValueError):
            loop_delay = 25
    elif active_app and active_app.startswith('anim_'):
        loop_delay = max(0.1, float(settings.get('anim_speed', '0.4')))
    else:
        loop_delay = 5

    stop_event.set()
    return jsonify(status=f"App {active_app} started")

@app.route('/stop_app', methods=['POST'])
def stop_app():
    global active_app
    active_app = None
    stop_event.set()
    return jsonify(status="stopped")

@app.route('/home_all')
def home_all():
    global is_homed, current_indices, current_display_string
    send_raw("m**h")
    is_homed = True
    current_indices = [0] * 45
    current_display_string = " " * 45
    return jsonify(status="Homing All")


# ============================================================
#  AUTO FINE-TUNE
# ============================================================

@app.route('/auto_tune', methods=['POST'])
def auto_tune_route():
    global is_homed, current_indices, current_display_string
    data   = request.json
    action = data.get('action')

    if action == 'home':
        send_raw("m**h")
        is_homed = True
        current_indices = [0] * 45
        current_display_string = " " * 45
        return jsonify(status="ok")

    elif action == 'goto_char':
        char_idx = int(data.get('char_index', 0))
        if 0 <= char_idx < len(FLAP_CHARS):
            ch = FLAP_CHARS[char_idx]
            # Build 45-char string of the same character and send raw
            # (raw=True so lowercase colour chars are not uppercased)
            text = ch * 45
            send_to_display(text, raw=True)
            return jsonify(status="ok", char=ch, index=char_idx)
        return jsonify(status="error", message="Invalid index"), 400

    elif action == 'adjust':
        modules   = data.get('modules', [])
        char_idx  = int(data.get('char_index', 0))
        delta     = int(data.get('delta', 0))
        adjusted  = []

        for mod_id in modules:
            mod_str = str(mod_id)
            cal     = int(settings['calibrations'].get(mod_str, 4096))
            expected = (char_idx * cal) // 64

            # Current value: tuned if available, else expected
            tuned_val = settings['tuned_chars'].get(mod_str, {}).get(str(char_idx))
            base = int(tuned_val) if tuned_val is not None else expected
            new_val = base + delta

            # Clamp to valid range
            if new_val < 0:
                new_val = 0
            if new_val >= cal:
                new_val = cal - 1

            # Update settings
            if mod_str not in settings['tuned_chars']:
                settings['tuned_chars'][mod_str] = {}
            settings['tuned_chars'][mod_str][str(char_idx)] = new_val

            # Write to firmware EEPROM
            send_raw(f"m{mod_id:02d}w{char_idx}:{new_val}")

            adjusted.append({'module': mod_id, 'old': base, 'new': new_val})

        save_settings(settings)
        return jsonify(status="ok", adjusted=adjusted)

    elif action == 'get_positions':
        char_idx = int(data.get('char_index', 0))
        positions = {}
        for i in range(45):
            mod_str  = str(i)
            cal      = int(settings['calibrations'].get(mod_str, 4096))
            expected = (char_idx * cal) // 64
            tuned    = settings['tuned_chars'].get(mod_str, {}).get(str(char_idx))
            positions[mod_str] = {
                'expected': expected,
                'tuned':    int(tuned) if tuned is not None else None,
                'active':   int(tuned) if tuned is not None else expected,
            }
        return jsonify(positions=positions)

    return jsonify(status="error", message="Unknown action"), 400


# ── Backup / Restore ─────────────────────────────────────────

@app.route('/backup_settings')
def backup_settings():
    return jsonify({
        'version':      1,
        'created':      datetime.now().isoformat(),
        'offsets':      settings['offsets'],
        'calibrations': settings['calibrations'],
        'tuned_chars':  settings['tuned_chars'],
    })

@app.route('/restore_settings', methods=['POST'])
def restore_settings():
    data = request.json
    if not data:
        return jsonify(status="error", message="No data"), 400
    if 'offsets'      in data: settings['offsets'].update(data['offsets'])
    if 'calibrations' in data: settings['calibrations'].update(data['calibrations'])
    if 'tuned_chars'  in data: settings['tuned_chars'].update(data['tuned_chars'])
    save_settings(settings)
    hw = False
    if ser:
        hw = True
        for i in range(45):
            s = str(i)
            send_raw(f"m{i:02d}o{int(settings['offsets'].get(s, 2832))}")
            send_raw(f"m{i:02d}t{int(settings['calibrations'].get(s, 4096))}")
            send_raw(f"m{i:02d}e")
            for idx, step in settings['tuned_chars'].get(s, {}).items():
                sv = int(step)
                if sv != 65535:
                    send_raw(f"m{i:02d}w{idx}:{sv}")
            logging.info(f"Restored m{i:02d}")
    return jsonify(status="success", hardware_updated=hw, modules_updated=45)

# ── Saved Playlists ──────────────────────────────────────────

@app.route('/playlists', methods=['GET', 'POST'])
def playlists():
    if request.method == 'GET':
        return jsonify(settings.get('saved_playlists', {}))
    data = request.json
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(status="error", message="Name required"), 400
    if 'saved_playlists' not in settings:
        settings['saved_playlists'] = {}
    settings['saved_playlists'][name] = {
        'pages': data.get('pages', []),
        'delay': data.get('delay', 5),
    }
    save_settings(settings)
    return jsonify(status="saved", name=name)

@app.route('/playlists/<path:name>', methods=['DELETE'])
def delete_playlist(name):
    plists = settings.get('saved_playlists', {})
    if name in plists:
        del plists[name]
        settings['saved_playlists'] = plists
        save_settings(settings)
    return jsonify(status="deleted")


if __name__ == '__main__':
    logging.info("Web UI running on 0.0.0.0:80")
    app.run(host='0.0.0.0', port=80)