# Lazy Link night mode: every RGB light OpenRGB can reach goes dark, and stays dark until UNTIL (HH:MM).
# Some devices (the cooler) fall back to their own lighting when no program talks to them, so keep re-applying.
import sys, time, datetime
sys.path.insert(0, [a for a in sys.argv[1:] if a.endswith('.whl')][0])
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor
import re
until = next((a for a in sys.argv[1:] if re.fullmatch(r'\d{1,2}:\d{2}', a)), None)
c = OpenRGBClient('127.0.0.1', 6742, 'lazy-link-night')
def dark():
    for d in c.devices:
        try:
            if 'off' in [m.name.lower() for m in d.modes]: d.set_mode('off')
            else: d.set_color(RGBColor(0, 0, 0))
        except Exception as e:
            print(f"{d.name}: {e}", flush=True)
dark(); print("dark:", ", ".join(d.name for d in c.devices), flush=True)
if until:
    h, m = map(int, until.split(':'))
    end = datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    if end <= datetime.datetime.now(): end += datetime.timedelta(days=1)
    while datetime.datetime.now() < end:
        time.sleep(30); dark()
