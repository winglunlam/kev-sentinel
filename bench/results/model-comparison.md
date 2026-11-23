| Model | Backend | Detection | False positives | p50 per check | Mean | Cost / 1,000 checks |
|---|---|---|---|---|---|---|
| — | rules only | 68/100 (68%) | 0/10 | 0.0 ms | 0.1 ms | $0 |
| kev-0.8b | kev + rules | 94/100 (94%) | 0/10 | 134.8 ms | 304.1 ms | $0.001521 |
| kev-0.5b | kev + rules | 88/100 (88%) | 2/10 | 297.5 ms | 303.9 ms | $0.00152 |
