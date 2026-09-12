from dataclasses import dataclass, asdict
import numpy as np
from scipy.special import gammainc


@dataclass(frozen=True)
class Decay:
    family: str = 'exponential'
    half_life: float = 7
    shape: float = 2
    ec: float = 3
    slope: float = 1
    max_lag: int = 90

    def __post_init__(self):
        if self.family not in ('exponential','weibull_survival','weibull_delayed'):
            raise ValueError('Unknown decay family')
        if not all(np.isfinite(v) and v>0 for v in (self.half_life,self.shape,self.ec,self.slope,self.max_lag)):
            raise ValueError('Decay parameters must be finite and positive')

    def weights(self, lag):
        lag=np.asarray(lag,dtype=float)
        x=np.maximum(lag,0)
        if self.family=='exponential':
            w=np.exp(-np.log(2)*x/self.half_life)
        elif self.family=='weibull_survival':
            w=np.exp(-np.log(2)*(x/self.half_life)**self.shape)
        else:
            # Discrete Weibull mass, peak-normalized on a FIXED lag grid.
            # half_life is the median of the underlying Weibull distribution here.
            s=self.half_life/np.log(2)**(1/self.shape)
            mass=lambda z: np.exp(-(z/s)**self.shape)-np.exp(-((z+1)/s)**self.shape)
            w=mass(x)/max(mass(np.arange(self.max_lag+1)).max(),1e-12)
        return np.where((lag>=0)&(lag<=self.max_lag),w,0)

    def saturate(self, x):
        x=np.maximum(np.asarray(x,dtype=float),0)
        z=(x/self.ec)**self.slope
        return z/(1+z)

    def key(self):
        return f'{self.family}:h{self.half_life}:s{self.shape}:ec{self.ec}:p{self.slope}'


def configurations(full=False):
    basic=[Decay('exponential',3,ec=2),Decay('exponential',10,ec=4),
           Decay('weibull_survival',7,shape=.7,ec=3),
           Decay('weibull_delayed',5,shape=2,ec=3)]
    if full:
        basic += [Decay(f,h,shape=s,ec=ec,slope=p) for f in ('exponential','weibull_survival','weibull_delayed') for h in (2,7,14) for s,ec,p in ((1.5,2,1),(2.5,5,2))]
    return basic
