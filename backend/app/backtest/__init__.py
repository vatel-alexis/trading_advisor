"""Historical simulation of the strategy on reconstructed option prices.

Yahoo has no option chain history, so each day's chain is rebuilt with Black-Scholes from the
underlying's close and a volatility proxy, then fed to the live screener and exit rules.
"""
