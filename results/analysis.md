## chronobox effect per model, all tasks without the CTF ones (34 tasks, 3 seeds)

| Model | Success alone | Success chronobox | Difference (95% CI) | Harmed alone | Harmed chronobox | Difference (95% CI) | Tasks better / worse |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | 96% | 96% | +0% [-7%, +7%] | 3% | 4% | +1% [-5%, +7%] | 3 / 3 |
| Opus 4.8 | 82% | 89% | +7% [-2%, +17%] | 13% | 7% | -6% [-15%, +2%] | 7 / 2 |
| Sonnet 5.5 | 72% | 83% | +12% [-1%, +25%] | 26% | 14% | -13% [-26%, +1%] | 9 / 2 |
| Sonnet 5 | 75% | 85% | +11% [+1%, +23%] * | 24% | 12% | -12% [-23%, -3%] * | 8 / 1 |
| Haiku 4.5 | 29% | 37% | +8% [-3%, +20%] | 55% | 43% | -12% [-24%, -1%] * | 10 / 5 |

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
