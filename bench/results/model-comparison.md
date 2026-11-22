| Model | Backend | Detection | False positives | p50 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| — | rules only | 68/96 (71%) | 0/10 | 0.0 ms | 0.1 ms | $0 |
| kev-0.8b | kev + rules | 90/96 (94%) | 0/10 | 134.7 ms | 398.7 ms | $0.001994 |
| kev-0.5b | kev + rules | 84/96 (88%) | 2/10 | 336.8 ms | 286.8 ms | $0.001434 |
