Measured 2026-11-23T14:03:50+00:00 on macOS-26.4.1-arm64-arm-64bit-Mach-O (arm, 3.13.14), model `kev-latest`.

| Backend | Detection | False positives | p50 per check | p95 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| rules only | 68/100 (68%) | 0/10 | 0.0 ms | 0.2 ms | 0.1 ms | $0 |
| kev + rules | 94/100 (94%) | 0/10 | 134.8 ms | 1183.4 ms | 304.1 ms | $0.001521 |

Cost is electricity only (60 W at $0.30/kWh); hardware amortization is excluded. Latency includes the local rules pass and one batched Kev request per endpoint.
