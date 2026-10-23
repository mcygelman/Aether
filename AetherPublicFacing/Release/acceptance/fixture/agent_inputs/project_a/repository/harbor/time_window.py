"""Shared UTC interval convention."""
from datetime import datetime
import re


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value, flags=re.ASCII):
        raise ValueError('UTC timestamp required')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')


def in_window(at, start, end):
    low, high = timestamp(start), timestamp(end)
    if low >= high:
        raise ValueError('Empty or reversed interval')
    return low <= timestamp(at) < high
