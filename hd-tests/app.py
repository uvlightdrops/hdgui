import plotly.express as px
from plotly.graph_objs import Scatter
import pandas as pd
from pandas import DataFrame
import matplotlib.pyplot as plt

from ephem_func import Astro


class App:

  def __init__(self):
    self.astro = Astro()
    self.gates = self.astro.gates

  def run(self):
    self.astro.test()
    #self.astro.now()
    self.get_data()

    self.visualize()
    self.plot()


  def get_data(self):
    self.timedict = self.astro.timedict
    self.gatedict = self.astro.gatedict
    self.bi = self.astro.bi
    #self.series = {}
    #self.series[0] = pd.Series(self.astro.gatedict['sun'], index=self.astro.timedict['sun'])
    # OK
    #self.df = pd.DataFrame(self.series[0])
    self.df = pd.DataFrame( [ self.astro.tdicts['sun'], self.astro.tdicts['mon'] ] )

  def plot(self):
    pass


  def visualize(self): 
    fig = {}
    sc = {}
    col   = {'sun': 'yellow','mon': 'blue', 'mer':'red'}
    name  = {'sun': 'Sun', '  mon': 'Moon', 'mer':'Mercury'}
    fig[0]  = px.scatter(self.df)
    fig[0].show()
    """
    for i in self.bi:
      #sc[i]   = Scatter(x=self.timedict[i], y=self.gatedict[i], name=name[i], \
                        #marker_color=col[i])
      #fig[i]  = sc[i]
      fig[i]  = px.scatter(x=self.timedict[i], y=self.gatedict[i])
      fig[i].show()
    """
    """
    fig.update_xaxes(
        #rangeslider_visible=True,
        rangeselector=dict(
            buttons=list([
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(count=6, label="6m", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1y", step="year", stepmode="backward"),
                dict(step="all")
            ])
        )
    )
    """



if __name__ == '__main__':
  app = App()
  app.run()

