# Experimental exact-model trainer

See [the operator guide](../docs/BROWSER-AND-TRAINING.md). Actual optimizer updates
and fresh-process adapter reload have executed on the exact Sumi 27B base.
No trained candidate is qualified for deployment or frontier-model parity.
No weights, adapters, local paths or private run artifacts are distributed.
Runtime provenance and license are in bonsai/. The lock file records the tested
WSL CUDA dependencies; install separately.

The coding pilot in pilot/ separates training, development and final-test
families before generating data. Its eight training fixtures passed 2,048
independent checks. These are small authored examples of familiar concepts,
not evidence of pretraining novelty or general engineering ability. Model
outputs are parsed by a bounded integer AST interpreter and never executed.
Do not tune training against the final-test score.
