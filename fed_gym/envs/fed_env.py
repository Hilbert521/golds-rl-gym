
import numpy as np
from gym.envs.registration import register

import gym
from gym import spaces
from .data import sampler


def register_solow_env(p, q):
    register(
        id='Solow-%s-%s-v0' % (p, q),
        entry_point='fed_gym.envs:SolowEnv',
        kwargs=dict(p=p, q=q)
    )
    register(
        id='Solow-%s-%s-finite-v0' % (p, q),
        entry_point='fed_gym.envs:SolowEnv',
        max_episode_steps=1024,
        kwargs=dict(p=p, q=q)
    )
    register(
        id='Solow-%s-%s-finite-eval-v0' % (p, q),
        entry_point='fed_gym.envs:SolowEnv',
        max_episode_steps=1024,
        kwargs=dict(p=p, q=q, seed=1692)
    )


class TickerEnvForTests(gym.Env):
    BUY_IDX = 1
    SELL_IDX = 2

    def __init__(self, starting_balance=10., n_assets=2):
        super(TickerEnvForTests, self).__init__()

        self.MIN_CASH = 1.
        self.n_assets = n_assets
        self.starting_balance = starting_balance

        self.cash_balance = None
        self.prices = None
        self.assets = None
        self.quantities = None

    def _step(self, action):

        discrete_choices = action[0]
        continuous_choices = action[1]
        assert len(discrete_choices) == len(continuous_choices) == self.n_assets
        q_add = np.zeros_like(continuous_choices)
        buy_mask = discrete_choices == self.BUY_IDX
        sell_mask = discrete_choices == self.SELL_IDX

        continuous_choices[buy_mask] /= max(continuous_choices[buy_mask].sum(), 1)

        q_add[buy_mask] = (continuous_choices * self.cash_balance / self.price)[buy_mask]
        q_add[sell_mask] = - continuous_choices[sell_mask] * self.quantities[sell_mask]

        self.quantities += q_add
        self.cash_balance += -(q_add * self.price).sum()

        old_assets = self.assets
        self.assets = self.cash_balance + np.sum(self.quantities * self.price)
        done = self.assets < self.MIN_CASH

        self.price = np.random.uniform(1, 2, size=(self.n_assets, ))
        self.volume = np.random.uniform(1, 2, size=(self.n_assets, ))

        return (
            np.hstack([self.cash_balance, self.quantities, self.price, self.volume]).flatten(),
            np.log(self.assets + 1e-4) - np.log(old_assets + 1e-4),
            done,
            {}
        )

    def _reset(self):
        self.cash_balance = self.starting_balance
        self.assets = self.cash_balance

        self.price = np.random.uniform(1, 2, size=(self.n_assets, ))
        self.volume = np.random.uniform(1, 2, size=(self.n_assets, ))

        self.quantities = np.zeros((self.n_assets, ))

        return np.hstack([self.cash_balance, self.quantities, self.price, self.volume])


class TickerEnv(gym.Env):
    BUY_IDX = 1
    SELL_IDX = 2

    def __init__(self, starting_balance=10., inverse_asset=True, n_assets=2):
        super(TickerEnv, self).__init__()

        self.MIN_CASH = 1.

        self.starting_balance = starting_balance
        self.n_assets = n_assets

        self.cash_balance = None
        self.prices = None
        self.assets = None
        self.quantities = None

        self.spread = 0.006 # 6 basis points

        self.data = sampler.OpenCloseSampler(ticker='IEF', inverse_asset=inverse_asset)
        self.data_idx = None

    def _step(self, action):

        discrete_choices = action[0]
        continuous_choices = action[1]
        assert len(discrete_choices) == len(continuous_choices) == self.n_assets
        q_add = np.zeros_like(continuous_choices)
        buy_mask = discrete_choices == self.BUY_IDX
        sell_mask = discrete_choices == self.SELL_IDX

        continuous_choices[buy_mask] /= max(continuous_choices[buy_mask].sum(), 1)

        q_add[buy_mask] = (continuous_choices * self.cash_balance / (self.prices * (1 + self.spread)))[buy_mask]
        q_add[sell_mask] = - continuous_choices[sell_mask] * self.quantities[sell_mask]

        self.quantities += q_add
        self.cash_balance += -(q_add * self.prices * (1 + self.spread))[buy_mask].sum() - (q_add * (self.prices * (1 - self.spread)))[sell_mask].sum()

        old_assets = self.assets
        self.assets = self.cash_balance + np.sum(self.quantities * self.prices)
        done = self.assets < self.MIN_CASH

        self.data_idx += 1
        self.prices = self.price_vol_data[self.data_idx, :2]
        self.volume = self.price_vol_data[self.data_idx, 2:]

        return (
            np.hstack([self.cash_balance, self.quantities, self.prices, self.volume]).flatten(),
            np.log(self.assets + 1e-4) - np.log(old_assets + 1e-4),
            done,
            {}
        )

    def _reset(self):
        self.cash_balance = self.starting_balance
        self.assets = self.cash_balance
        self.price_vol_data = self.data.sample(1024)

        self.data_idx = 0
        self.prices = self.price_vol_data[self.data_idx, :self.n_assets]
        self.volume = self.price_vol_data[self.data_idx, self.n_assets:]

        self.quantities = np.zeros(shape=(self.n_assets, ))

        return np.hstack([self.cash_balance, self.quantities, self.prices, self.volume])

    def _seed(self, seed=None):
        if seed:
            np.random.seed(seed)


class SolowEnv(gym.Env):
    """
    Classic Solow model (no growth or pop growth) with log consumption utility
    States are histories of capital and tech innovation/shock
    """
    def __init__(self, delta=0.02, sigma=0.1, p=1, q=1, T=None, seed=None):
        super(SolowEnv, self).__init__()

        self.delta = delta
        self.sigma = sigma
        self.alpha = 0.33

        self.seed = seed

        self.T = T if T else 2048

        self.p = p
        self.q = q
        if self.p > 0:
            self.rho_z = 0.5 ** np.arange(1, p + 1)
            self.rho_z /= self.rho_z.sum() / 0.95
        else:
            self.rho_z = 0.95
        if self.q > 0:
            self.rho_e = 0.5 ** np.arange(1, q + 1)
        else:
            self.rho_e = 0.5

        self.e = None
        self.z = None
        self.k = None

        self.action_space = spaces.Box(0, 1., shape=1)

    def _k_transition(self, k_t, y_t, s):
        return (1 - self.delta) * k_t + s * y_t

    def _k_ss(self, savings):
        return (savings / self.delta) ** (1 / (1 - self.alpha))

    def _step(self, s):
        s = max(1e-3, s)
        y_t = np.exp(self.z[-1]) * (self.k ** self.alpha)
        k_next = self._k_transition(self.k, y_t, s)

        e_t = self.es.pop()
        ar_component = self.rho_z * self.z
        try:
            ar_component = ar_component.sum()
        except AttributeError:
            pass
        ma_component = self.rho_e * self.e
        try:
            ma_component = ma_component.sum()
        except AttributeError:
            pass
        z_next = ar_component + ma_component + e_t

        if self.p > 0:
            self.z = np.array(self.z[1:].tolist() + [z_next])
        else:
            self.z = np.array([z_next])
        if self.q > 0:
            self.e = np.array(self.e[1:].tolist() + [e_t])
        else:
            self.e = np.array([e_t])
        self.k = k_next

        state = np.array([self.k, z_next]).flatten()
        reward = np.log((1 - s) * y_t + 1e-4)
        return (
            state,
            reward,
            False,
            {}
        )

    def _seed(self, seed=None):
        if seed:
            np.random.seed(seed)

    def _reset(self):
        self.k = self._k_ss(0.33)
        self.e = np.zeros(shape=(self.q, ))
        if self.seed:
            np.random.seed(self.seed)
        self.z = np.random.normal(scale=self.sigma, size=(self.p, ))
        self.es = np.random.normal(0, self.sigma, (self.T, )).tolist()

        return np.array([self.k, self.z[-1]]).flatten()


class SolowSSEnv(SolowEnv):
    def __init__(self, delta=0.02, sigma=0.02, T=None):
        super(SolowSSEnv, self).__init__(delta, sigma, p=1, q=0, T=T)

    def _reset(self):
        self.k = self._k_ss(self.alpha)
        self.z = np.array([0.])
        self.e = 0.
        if self.seed:
            np.random.seed(self.seed)
        self.es = np.random.normal(0, self.sigma, (self.T, )).tolist()

        return np.array([self.k, self.z]).flatten()


class TradeAR1Env(gym.Env):
    def __init__(self, starting_balance=10., base_rate=0.05, n_assets=2, std_p=0.05):
        super(TradeAR1Env, self).__init__()

        self.MIN_CASH = 1.

        self.starting_balance = starting_balance
        self.r = base_rate
        self.n_assets = n_assets
        self.rho_p = 0.9
        self.std_e = np.sqrt((std_p ** 2) * (1 - self.rho_p ** 2))

        self.cash_balance = None
        self.prices = None
        self.assets = None
        self.quantity = None
        self.e = None

        # fraction to sell = negative, fraction of funds used to purchase = positive
        self.action_space = spaces.Box(-1., 1., shape=(self.n_assets, ))
        self.observation_space = spaces.Tuple(
            [
                spaces.Box(0., np.inf, shape=(1, )), # funds
                spaces.Box(0., np.inf, shape=(2, )), # quantity
                spaces.Box(0., np.inf, shape=(n_assets, )) # price
            ]
        )

    def _price_transition(self, p):
        e_t = self.std_e * np.random.normal(size=(self.n_assets, ))
        return (p ** self.rho_p) * np.exp(e_t)

    def _step(self, action):
        assert self.action_space.contains(action)
        buy_mask = action > 0
        q_add = np.zeros_like(action)
        q_add[buy_mask] = ((action / self.n_assets) * self.cash_balance / self.prices)[buy_mask]
        q_add[~buy_mask] = (action * self.quantity)[~buy_mask]

        self.quantity += q_add
        self.cash_balance += -(q_add * self.prices).sum()

        old_assets = self.assets
        self.assets = self.cash_balance + np.sum(self.quantity * self.prices)
        done = self.assets < self.MIN_CASH

        self.prices = self._price_transition(self.prices)

        return (
            np.hstack([self.cash_balance, self.quantity, self.prices]).flatten(),
            np.log(self.assets + 1e-4) - np.log(old_assets + 1e-4),
            done,
            {}
        )

    def _reset(self):
        self.cash_balance = self.starting_balance
        self.assets = self.cash_balance
        self.prices = np.ones((self.n_assets, ))
        self.quantity = np.zeros((self.n_assets, ))
        self.e = np.zeros_like(self.quantity)

        return np.hstack([self.cash_balance, self.quantity, self.prices])

    def _seed(self, seed=None):
        if seed:
            np.random.seed(seed)
