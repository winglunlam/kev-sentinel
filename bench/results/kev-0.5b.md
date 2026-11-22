Measured 2026-11-22T13:45:53+00:00 on macOS-26.4.1-arm64-arm-64bit-Mach-O (arm, 3.13.14), model `kev-latest`.

| Backend | Detection | False positives | p50 per check | p95 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| rules only | 68/96 (71%) | 0/10 | 0.0 ms | 0.1 ms | 0.1 ms | $0 |
| kev + rules | 84/96 (88%) | 2/10 | 336.8 ms | 621.0 ms | 286.8 ms | $0.001434 |

Cost is electricity only (60 W at $0.30/kWh); hardware amortization is excluded. Latency includes the local rules pass and one batched Kev request per endpoint.
