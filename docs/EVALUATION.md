# Evaluation

Generated 2026-09-02 by `make eval`. Regenerate it and the
numbers change; that is the point of committing them with a date.

| | |
| --- | --- |
| Extraction model | `groq:openai/gpt-oss-120b` |
| Review model | `groq:qwen/qwen3.8-27b` |
| Independent review | **no** |
| Judge | **not configured** |

> The Skeptic is running on the same provider as the Analyst, because only
> one provider is configured. Adversarial review is therefore less
> independent than designed, and these precision numbers should be read
> as a floor rather than the intended result.

## Method

Three things this harness does that a demo scorecard usually does not.

**Abstention counts as success.** A system that refuses to name an owner it
cannot determine is behaving correctly. Scoring only correct answers would
mark that as a miss and push the system towards guessing, which is the exact
failure the design exists to prevent. Correct abstention is its own column.

**False positives are measured on an empty transcript.** `no_commitments` is a
retro where nobody agrees to anything. A summarizer asked for action items
will invent some, because the prompt implies they exist. Zero is the only
passing score.

**Matching is fuzzy.** An extracted item reading "Deliver the migration plan
to Meera by Tuesday" and a label reading "migration plan" are the same thing.
A harness that calls that a miss measures string formatting, not comprehension.

## Results

| Case | Precision | Recall | F1 | Owners right | Correctly abstained | Grounded | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `followup_slippage` | 1.00 | 1.00 | 1.00 | 0 | 0 | 2/2 | 7.4 |
| `hard_cases` | 1.00 | 0.00 | 0.00 | 0 | 0 | n/a | 48.4 |
| `no_commitments` | 1.00 | 1.00 | 1.00 | 0 | 0 | n/a | 30.5 |
| `standup_clean` | 1.00 | 0.67 | 0.80 | 2 | 0 | 3/3 | 15.3 |

**Grounding fidelity: 5/5 quotes verified against the source.** Every stored quote is text that genuinely appears in the transcript, because the verifier is a gate rather than a prompt instruction.

**False positives on a transcript with nothing in it: 0.** Correct.

## Failure gallery

Nothing failed on this run. That is a small gold set rather than a solved
problem: the honest reading is that the cases here are covered, not that
the system is correct in general.

## What is not measured here

- **Cross-meeting slippage** has labelled fixtures but needs the full graph and a
  database, so it is exercised by the integration path rather than this harness.
- **An LLM judge** would let the fuzzy matching above be replaced with something
  better calibrated. It needs a provider that did not produce the output, and
  only one provider is configured.
- **The gold set is small.** Four cases chosen for the behaviours they expose,
  not a sample large enough for confidence intervals.

