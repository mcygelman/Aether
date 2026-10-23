# Project B: multi-currency settlement report

Add `currency_report(rows, start, end)` to `harbor/reports.py` on this branch.
Rows contain `id`, `at` (UTC timestamp), `amount` (decimal string), and `currency`.
Return a list of exactly `{"currency": C, "count": N, "total_minor": N,
"ids": [...]}` groups. This deployment now supports USD (2 decimal places),
JPY (0) and KWD (3). Never mix currencies or return floating-point totals.
Omit empty groups. Validate every row, including rows outside the interval;
reject duplicate IDs, unsupported currencies, invalid amounts and intervals
with ValueError. Do not mutate caller inputs. Preserve existing APIs and follow
the conventions documented in this repository.

Deliver deterministic tests, docs/OPERATOR_GUIDE.md, IMPLEMENTATION.md and a
separately authored REVIEW.md with findings, evidence and their disposition.
Document the new currency rules and reproducible diagnostics. No network or
additional dependencies are needed.
