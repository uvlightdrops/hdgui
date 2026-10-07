from ephem import *
import datetime
import itertools
import math

zodiac = 'AR TA GE CN LE VI LI SC SG CP AQ PI'.split()
my_lat  = 48.378
my_long = 10.899
year = 2022
month = 5



def angle_set_time_dateobj(a):
    a = math.degrees(a) / 15.0
    hour = int(a)
    minute = int((a % 1) * 60)
    second = int(((a * 60) % 1) * 60)
    return (hour, minute, second)


class Astro:

  def __init__(self):
    gates = []
    with open('gates_list_zodiac_order.csv', 'r') as inp:
      for line in inp:
        gates.append(line.strip())
    self.gates = gates 
    self.observer = Observer()
    self.observer.lat   = my_lat
    self.observer.long  = my_long
    self.timedict = {}
    self.gatedict = {}
    #self.bodies[Sun(), Moon(), Mercury(), Venus(), Mars(), Jupiter(), Saturn(), Uranus(), Neptune(), Pluto()]
    self.bodies = {}
    self.bodies['sun'] = Sun()
    self.bodies['mon'] = Moon()
    self.bodies['mer'] = Mercury()
    self.bodies['ven'] = Venus()
    self.bodies['mar'] = Mars()
    self.bodies['jup'] = Jupiter()
    self.bodies['sat'] = Saturn()
    self.bodies['ura'] = Uranus()
    self.bodies['nep'] = Neptune()
    self.bodies['plu'] = Pluto()
    self.bi = ['sun','mon','mer','ven','mar','jup','sat','ura','nep','plu']
    self.tdicts = {}
    for i in self.bi:
      self.timedict[i] = []
      self.gatedict[i] = []
      #self.serie[i] = pd.Series()
      self.tdicts[i] = {}

  def test(self):
    self.print_ephemeris_for_month(2022,5)
    #self.print_ephemeris_for_year(2022)

  def now(self):
    #self.ecliptic_for_hour()
    d = datetime.datetime.now()
    self.result = self.print_ephemeris_for_date(d)

  def gate_from_longitude(self, longitude):
      l = math.degrees(longitude.norm)
      gate_index = int(l/(360/64))
      gate = self.gates[gate_index]
      return gate

  def ecliptic_for_hour(self, d):
    for i in range(24):
      d = datetime.datetime(d.year, d.month, d.day, i,0,0)
      self.observer.date = d
      for i in self.bi:
        b = self.bodies[i]
        b.compute(self.observer)
        l = Ecliptic(b).long
        gate = self.gate_from_longitude(l)
        #print(i, gate)
        self.timedict[i].append(d)
        self.gatedict[i].append(gate)

        self.tdicts[i][d] = gate 

  def print_ephemeris_for_date(self, date):
    date = Date(date)
    print(date)
    d2 = date.datetime()
    print('date', date)
    #self.observer.date = date
    data = self.ecliptic_for_hour(d2)

  def print_ephemeris_for_month(self, year, month):
    print(year, month)
    for day in itertools.count(1):
      try:
        datetuple = (year, month, day)
        d = datetime.date(*datetuple)
        self.print_ephemeris_for_date(d)
      except ValueError:
        break

  def print_ephemeris_for_year(self, year):
    for month in range(1, 13):
      self.print_ephemeris_for_month(year, month)

