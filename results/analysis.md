## chronobox effect per model (34 tasks without the CTF ones, 3 seeds)

| Model | Success alone | Success chronobox | Difference (95% CI) | Harmed alone | Harmed chronobox | Difference (95% CI) | Tasks better / worse |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | 95% | 94% | -1% [-9%, +7%] | 4% | 6% | +2% [-6%, +10%] | 4 / 4 |
| Opus 4.8 | 81% | 89% | +8% [-2%, +19%] | 14% | 7% | -7% [-17%, +2%] | 8 / 3 |
| Sonnet 5.5 | 70% | 84% | +15% [+4%, +26%] * | 28% | 13% | -16% [-29%, -4%] * | 9 / 1 |
| Sonnet 5 | 75% | 85% | +10% [-1%, +22%] | 23% | 12% | -11% [-22%, -1%] * | 8 / 2 |
| Haiku 4.5 | 29% | 39% | +10% [+0%, +21%] | 55% | 43% | -12% [-24%, -1%] * | 11 / 4 |

\* the 95% interval excludes 0.

## CTF tasks and model safeguards

| Model | Setup | Runs | Success | Stopped by the model's safeguards |
|---|---|---|---|---|
| Opus 5.5 | alone | 1 | 0 | 1 |
| Opus 4.8 | alone | 9 | 9 | 0 |
| Opus 4.8 | chronobox | 9 | 9 | 0 |
| Sonnet 5.5 | alone | 3 | 0 | 3 |
| Sonnet 5.5 | chronobox | 3 | 0 | 3 |
| Sonnet 5 | alone | 9 | 9 | 0 |
| Sonnet 5 | chronobox | 9 | 9 | 0 |
| Haiku 4.5 | alone | 9 | 7 | 0 |
| Haiku 4.5 | chronobox | 9 | 6 | 0 |

Safeguard stops outside the CTF tasks: 0
