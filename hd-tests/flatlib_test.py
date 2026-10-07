from flatlib.geopos import GeoPos
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib import const

date = Datetime('2015/03/13', '17:00', '+00:00')
pos = GeoPos('38n32', '8w54')
chart = Chart(date, pos)

sun = chart.get(const.SUN)
print(sun)
