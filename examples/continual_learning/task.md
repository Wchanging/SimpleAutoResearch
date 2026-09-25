# Prepared-project research acceptance

Use the existing Mammoth checkout and cached CIFAR-100 only. The measurement adapter and its entry command are engineering-provided infrastructure, not autonomous discoveries. The initial one-epoch protocol is a low-cost framework acceptance condition, not a faithful reproduction or publication-quality training schedule. Use all ten tasks; do not shorten the task sequence or alter metrics to claim success.

Choose the implementation location after inspecting the project. Do not assume a required patch in models/er.py. Modify only authorized method/support files, not the evaluator, dataset, split, logging or metric definitions. Preserve the ER entry interface used by the adapter. No downloads, dependency installation, external datasets or training on held-out test data.

Use the same fixed validation permutation, class order, training schedule and memory budget for comparable baseline/candidate conditions. The source split must be prepared and verified before this case starts; do not independently generate a different split in each workspace. Historical results are not reusable until source and protocol comparability are established.

Investigate forgetting together with accuracy and backward transfer. A larger final accuracy alone does not establish reduced forgetting. Justify seed count and any supplement within the physical budget; API cumulative usage is unlimited, but at most two research follow-up rounds and bounded technical retries are authorized. Do not create extra rounds solely to demonstrate the loop. Report failed methods and uncertainty without claiming statistical significance from a single seed.

The supplied 1800-second per-process and 7200-second total process limits are provisional ceilings, not measured runtime estimates. Complete preflight before launch. Research preparation, training and reporting must remain in the same normal CLI session; any developer source repair during the run makes it diagnostic rather than frozen acceptance.
