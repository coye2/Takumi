# Training settings sweep

Written before any sweep result was seen. The earlier run used learning rate 0.0003 at scale 4.0 and was rejected.

| Run | Learning rate | Scale |
| --- | --- | --- |
| a | 0.00003 | 1.0 |
| b | 0.0001 | 1.0 |
| c | 0.00003 | 4.0 |

Each run trains 64 steps and scores the 11 `dev` tasks at step 28 and step 64.

## Selection rule

1. A checkpoint qualifies only if it passes **more** dev tasks than untrained Sumi and has no more replies that exhaust the generation budget.
2. Among those, take the most dev passes. Ties go to the earlier checkpoint, then the smaller learning rate times scale.
3. Only the selected checkpoint is scored on `holdout2`, once, beside untrained Sumi.
4. If nothing qualifies, no adapter is selected and `holdout2` is not used for training at all. That result is reported as it is.

Eleven tasks can only show large effects. A win here justifies a bigger test, not deployment.
