# Data conventions

Amounts are nonnegative plain ASCII decimal strings: one or more digits,
optionally followed by a period and one or more digits. No sign, exponent,
whitespace, NaN or infinity. Reject fractional digits beyond the currency scale;
do not round or silently truncate. The supported range is 0 through 999999999
minor units, inclusive. Internal and public totals use integer minor units.

Timestamp strings have exactly YYYY-MM-DDTHH:MM:SSZ form and valid calendar dates.
All report windows are UTC and half-open: start is included; end is excluded.
Require start < end. A row ID is a nonempty ASCII string of letters, digits,
underscore or hyphen. IDs are unique across the whole input. Sort IDs and
currency groups by ordinary ASCII lexical order, independent of input order.

Every row has exactly id, at, amount and currency. Validate the complete batch
before selecting the window. Empty input is valid. Existing helpers in money.py
and time_window.py are shared by other features; preserve their signatures.

This branch supports USD with two decimal places.
