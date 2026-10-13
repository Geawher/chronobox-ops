## chronobox effect per model, all tasks without the CTF ones (38 tasks, 3 seeds)

| Model | Success alone | Success chronobox | Difference (95% CI) | Harmed alone | Harmed chronobox | Difference (95% CI) | Tasks better / worse |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | 96% | 96% | +0% [-6%, +6%] | 3% | 4% | +1% [-4%, +6%] | 3 / 3 |
| Opus 4.8 | 84% | 90% | +6% [-2%, +15%] | 11% | 6% | -5% [-13%, +1%] | 7 / 2 |
| Sonnet 5.5 | 75% | 85% | +11% [-1%, +23%] | 24% | 12% | -11% [-25%, +1%] | 9 / 2 |
| Sonnet 5 | 75% | 86% | +11% [+1%, +20%] * | 23% | 11% | -11% [-21%, -3%] * | 9 / 1 |
| Haiku 4.5 | 32% | 38% | +6% [-4%, +17%] | 54% | 45% | -9% [-19%, +2%] | 10 / 6 |

\* the 95% interval excludes 0.

## chronobox effect on the four rehearsal tasks (4 tasks, 3 seeds)

| Model | Success alone | Success chronobox | Difference (95% CI) | Harmed alone | Harmed chronobox | Difference (95% CI) | Tasks better / worse |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | 100% | 100% | +0% [+0%, +0%] | 0% | 0% | +0% [+0%, +0%] | 0 / 0 |
| Opus 4.8 | 100% | 100% | +0% [+0%, +0%] | 0% | 0% | +0% [+0%, +0%] | 0 / 0 |
| Sonnet 5.5 | 100% | 100% | +0% [+0%, +0%] | 0% | 0% | +0% [+0%, +0%] | 0 / 0 |
| Sonnet 5 | 83% | 92% | +8% [-8%, +33%] | 17% | 8% | -8% [-33%, +8%] | 1 / 0 |
| Haiku 4.5 | 50% | 42% | -8% [-33%, +0%] | 42% | 58% | +17% [+0%, +42%] | 0 / 1 |

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
