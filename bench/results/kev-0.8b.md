Measured 2026-11-22T13:22:32+00:00 on macOS-26.4.1-arm64-arm-64bit-Mach-O (arm, 3.13.14), model `kev-latest`.

| Backend | Detection | False positives | p50 per check | p95 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| rules only | 68/92 (74%) | 0/10 | 0.0 ms | 0.1 ms | 0.0 ms | $0 |
| kev + rules | 82/92 (89%) | 0/10 | 126.9 ms | 2432.7 ms | 459.6 ms | $0.002298 |

Cost is electricity only (60 W at $0.30/kWh); hardware amortization is excluded. Latency includes the local rules pass and one batched Kev request per endpoint.
