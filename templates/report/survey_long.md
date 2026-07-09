# Long Survey Report Template

## Intended Use

Use this template for research-only runs that need a long, reader-oriented
academic survey rather than a compact technical brief. It is suitable for
SurveyBench-style evaluation, thesis-style literature review, and topics where
coverage, taxonomy, applications, evaluation, and challenges matter more than
concise reporting.

Do not write this as a pipeline run log. Search provenance, artifact paths,
tool internals, extraction/debug details, and stage names belong in run
artifacts, not in the report body.

## Writing Workflow

Write a field survey, not a paper-note summary. The report should:

- start from a topic-specific outline rather than forcing every topic into the
  same narrow taxonomy;
- cover foundations, construction methods, applications, evaluation practice,
  related surveys, challenges, and future directions;
- use the source set broadly and cite representative sources near specific
  claims;
- include comparison tables for taxonomies, applications, benchmarks, and
  challenges when evidence permits;
- include figure-ready conceptual diagrams as Mermaid or structured Markdown
  diagram specifications when useful, without inventing nonexistent image
  files;
- explicitly distinguish established findings, survey-level synthesis,
  plausible hypotheses, and open questions.

Target length is intentionally larger than the compact `survey` template. When
source coverage is sufficient, sections should be substantial enough to answer
reader questions, not merely name method families.

Draft order: Foundations And Taxonomy -> System Construction -> Applications And Domains -> Evaluation And Benchmarks -> Related Surveys And Positioning -> Challenges And Open Problems -> Future Directions -> Conclusion -> Introduction -> Abstract

## Abstract

Write this after the body is drafted. Summarize the topic, scope, organizing
taxonomy, main technical trends, evaluation state, and open challenges. Keep it
compact but information-rich. Do not describe the generation process.

## Introduction

Introduce the field, why it matters, and what reader needs the survey addresses.
Define the topic boundary, major subareas, and the survey's organization. Make
the scope clear without apologizing for every missing paper.

## Foundations And Taxonomy

Explain the core concepts and build a taxonomy that a newcomer can use to
navigate the area. Include a table with major axes when evidence permits, such
as model architecture, data source, training/adaptation method, coordination
mechanism, evaluation setting, or deployment constraint.

If useful, include a figure-ready conceptual map as a Mermaid diagram or a
compact structured diagram block. Do not use a Markdown image link unless a
real generated image file exists.

## System Construction

Describe how systems in this area are built. Cover architectures, modules,
training or prompting strategies, data pipelines, memory/context mechanisms,
tool use, coordination protocols, and implementation patterns where relevant.
Compare approaches across papers instead of listing papers one by one.

## Applications And Domains

Map the main application domains and use cases. For each domain, explain the
task setting, why the surveyed methods are useful, what evidence supports them,
and what limitations remain. Include an application matrix when possible.

## Evaluation And Benchmarks

Summarize how the field evaluates progress. Discuss datasets, benchmarks,
metrics, evaluation protocols, baselines, reproducibility, cost, and known
failure cases. Include a benchmark/evaluation table whenever enough evidence is
available. Be explicit about whether results are directly comparable.

## Related Surveys And Positioning

Position the surveyed work relative to existing surveys and neighboring fields.
Explain what prior surveys emphasize, what this synthesis adds, and how the
topic connects to adjacent areas. This section should group related surveys by
role rather than list them chronologically.

## Challenges And Open Problems

Synthesize unresolved challenges. Cover technical limitations, evaluation gaps,
deployment risks, data/resource bottlenecks, robustness, safety, interpretability,
and human or social constraints when relevant. Tie each challenge to evidence or
clear reasoning from the source set.

## Future Directions

State concrete research directions and testable hypotheses. Prefer actionable
directions over generic "more research is needed" statements. When proposing a
direction, say what evidence would confirm or falsify it.

## Conclusion

Close with the survey's main takeaway, the state of the field, and the most
important next step for researchers or practitioners.
