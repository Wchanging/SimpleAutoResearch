# Local Evidence Notes: Lightweight Text Classification

These notes are intentionally short and local. They are used by the greenfield
example so the research stages have something task-relevant to pass into
design, without downloading papers or using external datasets.

## Practical Baselines

For tiny text classification tasks, a majority-class baseline is often too weak
to diagnose whether a model learned lexical signal. A keyword-rule baseline is
still simple, but more meaningful when class names are associated with a few
obvious indicator words. A generated experiment should report baseline accuracy
alongside model accuracy so improvements are interpretable.

## Simple Models

Bag-of-words multinomial Naive Bayes is a good lightweight CPU-only method for
small deterministic text tasks. It can be implemented with standard-library
token counting, Laplace smoothing, class priors, and log probabilities. It also
has an auditable parameter count: roughly class priors plus class-token
likelihoods over the vocabulary.

For a more useful greenfield engineering test, the implementation should not
stop at one model file. A small but realistic local suite can separate data
generation, feature extraction, model definitions, evaluation aggregation,
metric calculation, and CLI output. This keeps the project inspectable while
still forcing the generator to preserve cross-file interfaces.

## Ablation Design

A useful local experiment should compare at least two model conditions under the
same train/test split. A practical pair is unigram features versus unigram plus
bigram features. This keeps the project lightweight while creating a real
experimental question: do local phrase features improve category prediction
over simple token counts?

When budget allows, add a stronger condition matrix: majority baseline,
keyword-rule baseline, unigram Naive Bayes, unigram+bigram Naive Bayes, and a
small character n-gram or smoothing ablation. The final metric set should expose
both best-model quality and condition-level results, so an automated report can
state whether the gain came from lexical modeling, phrase features, or merely an
easy synthetic split.

## Dataset Difficulty

Perfect accuracy is not very informative. The synthetic dataset should include
some controlled noise, distractor terms, and overlapping vocabulary between
classes. The goal is not to make the task hard, but to avoid a trivial split
where every model reaches 1.0 and the ablation metric becomes meaningless.

## Reporting Guidance

The generated project should emit parseable metrics for the best condition and
also expose condition-level metrics. The final claim should be bounded: if the
best model improves over the baseline on this deterministic synthetic dataset,
that demonstrates the local implementation works, not that the method is
generally superior.
