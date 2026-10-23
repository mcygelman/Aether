# Project A: daily settlement report

Add `daily_report(rows, start, end)` to `harbor/reports.py`.
Rows contain `id`, `at` (UTC timestamp), `amount` (decimal string), and `currency`.
This branch supports USD only. Return exactly `{"count": N, "total_minor": N,
"ids": [...]}` for the selected interval. Validate every row, including rows
outside the interval. Reject duplicate IDs, unsupported currencies, invalid
amounts and invalid intervals with ValueError. Do not mutate caller inputs.
Follow this repository's conventions and preserve existing APIs.

Deliver deterministic tests, docs/OPERATOR_GUIDE.md, IMPLEMENTATION.md and a
separately authored REVIEW.md with findings, evidence and their disposition.
The guide must explain running the tests, the API and diagnosing unexpected totals.
The implementation report must identify changed files and test commands/results.
Retain the first contributor candidate for independent review before correcting
review findings. No network or additional dependencies are needed.
