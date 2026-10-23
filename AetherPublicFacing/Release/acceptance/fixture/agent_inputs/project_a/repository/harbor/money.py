"""Legacy amount conversion shared by settlement features."""
import re


def parse_minor(amount, scale=2):
    if type(scale) is not int or not 0 <= scale <= 3:
        raise ValueError('Invalid scale')
    if not isinstance(amount, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', amount):
        raise ValueError('Invalid amount')
    fraction = amount.partition('.')[2]
    if len(fraction) > scale:
        raise ValueError('Excess fractional digits')
    minor = int(float(amount) * 10 ** scale)
    if not 0 <= minor <= 999999999:
        raise ValueError('Amount out of range')
    return minor
