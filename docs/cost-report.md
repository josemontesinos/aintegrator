# AIntegrator cost report

All figures are derived from the deterministic offline eval report. They use fictional
pricing and cassette token counts; the monthly number is a planning assumption, not
observed spend.

## Pricing and volume

- Model: `claude-sonnet-5-5`
- Input: $3.00 / 1M tokens
- Output: $15.00 / 1M tokens
- Observed eval runs: 102
- Planning volume: 100 runs/day x 30 days = 3000 runs/month

## Per-run cost

| Metric | Value |
| --- | --- |
| Input tokens / run | 387.06 |
| Output tokens / run | 124.41 |
| Estimated cost / run | $0.003027 |
| Projected monthly cost | $9.081 |

## By tool profile

| Profile | Runs | In/run | Out/run | Cost/run | Cost / 1k runs |
| --- | --- | --- | --- | --- | --- |
| read-only | 9 | 326.67 | 105.00 | $0.002555 | $2.555 |
| standard | 93 | 392.90 | 126.29 | $0.003073 | $3.073 |

## By provider

| Provider | Runs | In/run | Out/run | Cost/run | Cost / 1k runs |
| --- | --- | --- | --- | --- | --- |
| all | 18 | 280.00 | 90.00 | $0.002190 | $2.190 |
| fibernet | 60 | 448.00 | 144.00 | $0.003504 | $3.504 |
| legacytel | 3 | 420.00 | 135.00 | $0.003285 | $3.285 |
| none | 18 | 280.00 | 90.00 | $0.002190 | $2.190 |
| skylink | 3 | 420.00 | 135.00 | $0.003285 | $3.285 |

## Findings

- Most expensive profile per run: `standard` at $0.003073.
- Cheapest profile per run: `read-only` at $0.002555.
- Read-only runs save $0.000518 per run versus `standard` on these cassettes.
- Costs are estimates from fictional rates; latency and real provider spend are excluded.

Regenerate with `make cost-report`; CI verifies the checked-in copy with
`make cost-report-check`. No API key or running services are needed.
