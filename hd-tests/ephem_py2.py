import ephem
from datetime import datetime

augsburg = ephem.Observer()
augsburg.lat = 48.378
augsburg.lon = 10.899

now = datetime.now()
nf = now.strftime('%Y/%m/%d %H:%M:%S')
augsburg.date = nf
moon = ephem.Moon()

moon.compute(augsburg)
print(moon.az, moon.alt)

