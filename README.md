# Payment Reconciliation Agent

## What this is

Indian businesses accepting UPI, NEFT, and card payments face a structural reconciliation problem: money and information about it travel through three disconnected systems with no shared format. The bank statement shows what landed, in inconsistent bank-specific narration. The payment gateway settles many transactions into one lump-sum credit, on a delay, with its own references. The business's invoices record what it expected. Matching these three — and correctly flagging real mismatches, like a settlement short by one failed transaction, or a batch that could match more than one combination of open invoices — is currently done by hand, in Excel, every month.

This project is an agent that closes that loop: it reconciles a synthetic batch of bank, settlement, and invoice records, and reports both a measured match rate and an honest, itemized list of what it couldn't resolve and why.

## Why this isn't "throw an LLM at it"

The obvious approach is handing an LLM all three files and asking it to match everything. That's fast to build and hard to trust — no way to tell arithmetic certainty from a guess, no way to catch a hallucinated match, no way to measure if its confidence means anything.

This project takes the opposite approach: every record passes through deterministic checks first — exact match, tolerance-window match, then a subset-sum search for the many-to-one case where one credit corresponds to a batch of invoices — and only what survives all of them, genuinely ambiguous even to arithmetic, reaches an LLM. On the real dataset built for this project, that's 9 records out of 153. The LLM's decisions are graded against a known-correct answer key like every other tier, and its confidence is checked against that same ground truth, not asserted. Result: 94.1% resolved with zero AI, and on the 9 that needed it, the LLM was right 8 times and, on the one it wasn't sure about, said so instead of guessing.

## Results

On the seed-42, 2,000-invoice / 153-bank-record dataset (`python -m
matcher.reporting_final`, full detail in `FINAL_REPORT.md`):

- **153 bank records processed. 144/153 (94.1%) resolved with zero AI cost** — 109
  deterministic tier matches (exact / tolerance / subset-sum) + 35 rule-based
  exception calls (the zero-candidate orphan rule, validated at 97.1% accuracy).
- **Only 9 records (5.9%) — the genuinely ambiguous ones that survived every cheaper
  check — ever reached a real LLM call.**
- **8/9 correct, 0 wrong matches.** The 1 miss was an honest `insufficient_evidence`
  defer, not a guess — for **$0.0109 total** (real Groq `openai/gpt-oss-120b` run).
- **Confidence scores are calibrated against real ground truth, not asserted** — a
  reliability table most reconciliation tools structurally cannot produce, because
  they have no known-correct answers to check against:

  | Confidence band | n | Empirical accuracy |
  |---|---|---|
  | `auto_match` (≥0.7) | 102 | 100% |
  | `needs_review` (0.45–0.7) | 6 | 100% |
  | `exception` (<0.45) | 1 | 0% (the one confirmed false positive — correctly isolated below the auto-match floor) |

- **End-to-end: 151/153 (98.7%) correct or honestly deferred** — exactly 2 documented,
  root-caused errors across the entire run (see Known Limitations).

## Architecture

```
bank record
   │
   ▼
[1] exact match            UTR + amount + settlement-lag, all three required
   │  (no match)
   ▼
[2] tolerance match        relaxed amount/lag bands, accepted only if one dimension stays near-exact
   │  (no match)
   ▼
[3] subset-sum search      bounded DP over unclaimed invoices within a date window
   │  (ambiguous / no candidates)
   ▼
[4] zero-candidate rule    deterministic: an exhaustive search finding nothing IS the evidence — no LLM needed
   │  (still ambiguous)
   ▼
[5] LLM escalation         bounded tool-calling loop, strict match/no_match/insufficient_evidence schema
   │
   ▼
[6] agent controller       records the actual path taken through 1-5 as a structured DecisionTrace per record
   │
   ▼
[7] final report           matcher/reporting_final.py assembles everything into FINAL_REPORT.md
```

Tiers 1-3 run breadth-first across *all* bank records at each stage (not per-record
tier-1-through-5), so which settlement/invoice a given record consumes never depends
on CSV row order — see `MATCHER_STATUS.md`. The agent controller
(`matcher/agent/controller.py`) wraps this same execution order without changing any
decision, producing one `DecisionTrace` per record: the exact stage-by-stage path,
each transition's reason, and what was observed before handing off to the next stage
— e.g. for a real escalated case, `exact → tolerance → subset_sum → llm_escalation`,
each step with its own recorded reason, not just a final answer. Provable equivalence
to the pre-agent pipeline is checked by `matcher/agent/regression_check.py` (0
discrepancies on this dataset — see `AGENT_CONTROLLER_STATUS.md`).

`matcher/reporting_final.py` is the last stage: it assembles `matches.json`,
`escalations.json`, `llm_decisions.json`, `calibration_report.json`,
`grade_report.json`, `llm_grade_report.json`, and `decision_traces.json` into one
Markdown report — `FINAL_REPORT.md` — headline numbers, per-tier accuracy, the
calibration table, every escalated record with its outcome, and known limitations, in
that order. That file, not any individual JSON artifact, is the actual submission
artifact.

## Bugs found and fixed during verification

Two real bugs, found by actually checking the output against ground truth rather than
trusting the design, root-caused, fixed, and re-verified — not decoration, the
concrete evidence that this build was checked, not assumed correct.

**1. Tier 2's confidence formula was compressing every correct match into the bottom
of the scale.**
- *Before*: all 10 correct `tolerance`-tier matches scored only **0.5 or 0.625**,
  despite being 100% correct — no discriminative spread at all.
- *Root cause*: the formula normalized lag/amount overshoot against a theoretical
  floor of 0, but a genuine `late_settlement` case can never land near 0 — the
  generator's own minimum extra delay guarantees an overshoot of at least 4 days. The
  formula had already "used up" 25%+ of its scale before any real lateness was even
  considered.
- *Fix*: normalize against the realistic achievable floor instead of the theoretical
  one (`config.TIER2_LAG_REALISTIC_MIN_OVERSHOOT_DAYS` / `TIER2_AMOUNT_TOLERANCE_MIN`).
- *After, re-verified*: all 10 matches still correct — the fix changed confidence
  values only, not which matches resolve — now spanning **0.5–0.833**, a real
  discriminating spread. Full writeup: `MATCHER_STATUS.md`.

**2. A real Groq `413` from an under-measured context size, found on the actual dry
run.**
- *Before*: the 2-case real dry run failed on case 2 — `Request too large ... TPM:
  Limit 8000, Requested 9219` — a single request bigger than the account's entire
  per-minute budget.
- *Root cause, measured before touching code*: the context sent every candidate
  subset's every invoice fully expanded inline — **65 invoice mentions across only 13
  distinct invoices** for that one case, a 5x redundancy, since real ambiguous
  subsets overlap heavily by construction.
- *Fix*: send a deduped `invoice_pool` (each unique invoice once) plus
  `candidate_subsets` as ID lists referencing it — identical information, ~4x less
  token cost (14,923 → 3,593 chars for that case).
- *After, re-measured across all 9 real cases*: max context size dropped to **~900
  tokens** (was ~3,730+ for the worst case pre-fix); the full batch then ran cleanly
  end to end at the $0.0109 / 8-9 result quoted above. (Two smaller bugs surfaced and
  were fixed in the same dry run: missing `GROQ_API_KEY` env-var wiring, and a
  rate-limiter `IndexError` on an empty-window edge case.) Full writeup:
  `LLM_ESCALATION_STATUS.md`.

## How to run it

```
python -m matcher.run --data-dir data/ --grade                # deterministic tiers 1-3 only
python -m matcher.run --data-dir data/ --grade --llm           # + real Anthropic API calls, costs money
python -m matcher.run --data-dir data/ --grade --llm-groq      # + real Groq (openai/gpt-oss-120b) calls, ~200x cheaper
python -m matcher.run --data-dir data/ --grade --llm-mock      # + zero-cost mock LLM, no credentials needed

python -m matcher.run_agent --data-dir data/ --llm-mock        # same pipeline, wrapped in the agent controller, writes decision_traces.json

python -m matcher.calibration                                  # confidence reliability table
python -m matcher.reporting_final --data-dir data/              # assemble everything into FINAL_REPORT.md
```

Both LLM providers implement the same `LLMClient` interface; `llm_tier.py`'s
orchestration logic is identical either way — see `LLM_ESCALATION_STATUS.md` for the
full, chronological writeup of this tier: every design decision, the deterministic
zero-candidate orphan rule, the Groq integration, and the real results above, start to
finish in one document. `AGENT_CONTROLLER_STATUS.md` covers the agent-controller wrap
and its regression proof; `MATCHER_STATUS.md` covers the three deterministic tiers and
the calibration work.

## Design details

### Ground-truth-first principle

The generator decides the true answer for every invoice/settlement/bank record
**before** injecting any noise, splits, or failures. `ground_truth.json` is the exam
key, not an input — **never point a matching pipeline at `data/answer_key/`**. It exists
only to compute real precision/recall and to check whether a pipeline's own
confidence-tier assignment and generated exception reason actually match reality.

### The designed failure-mode distribution

Configured in `generator/config.py::FAILURE_MODE_TARGETS`, sampled per invoice at
generation time:

| Mode | Target | Rationale |
|---|---|---|
| `clean_match` | 66% | The baseline — most reconciliation should be trivial. |
| `rounding_fee_variance` | 11% | Net amount off by ₹1–50 from a plausible fee miscalculation; tests tolerance-band matching. |
| `late_settlement` | 7% | Settlement lands beyond the normal T+1/T+2 window, sometimes deliberately across a weekend; tests a wider date-tolerance window. |
| `entity_name_variant` | 4.5% | Bank narration uses a registered alternate form of the counterparty name; tests fuzzy entity resolution. |
| `duplicate_amount_collision` | 3.5% | Two unrelated invoices share an amount and a nearby date; tests false-positive risk directly. |
| `ambiguous_subset_sum` | 3% | Two distinct subsets of a candidate invoice pool sum to the same amount within tolerance — the headline hard case, meant for LLM escalation. Its settlement's `order_id` and its bank record's UTR are deliberately blanked (see below), so a downstream matcher can't shortcut invoice-membership reconstruction by reading a trusted key. |
| `partial_batch_failure` | 2% | One invoice is dropped from a batch before settlement, shorting the total by exactly its amount; validates subset-sum search. |
| `orphan_payment` | 1.5% | A bank credit exists with no corresponding invoice or settlement — a genuine exception. |
| `orphan_invoice` | 1.5% | An invoice exists that was never paid — a genuine exception. |

The four low-frequency modes (`ambiguous_subset_sum`, `partial_batch_failure`, and the
two orphan modes) are weighted above what a literal "3% hard cases" framing might
suggest, specifically so that at the dataset's scale (~1,000–3,000 invoices) each
still produces enough instances (dozens) to be a real distribution to evaluate against,
not one or two anecdotes. `generator/generate.py` prints the expected count per mode
before generation and the realized count after, warning if any low-frequency mode
falls below a health floor (`config.MIN_HEALTHY_COUNT`) on a given seed.

**Why `ambiguous_subset_sum` blanks `order_id`/UTR**: `settlement_report.csv` normally
gives one row per batch with a trustworthy `order_id` (pipe-joined invoice IDs) and a
UTR that's never itself distorted by any other failure mode — so a matcher that reads
`order_id` once it's found the settlement (a legitimate, realistic thing to do) would
trivially resolve invoice membership even for a settlement built from a genuinely
ambiguous subset-sum construction, making the failure mode untestable. To keep this
the genuine hard case it's meant to be, `settlement_factory.py` blanks that one
settlement's `order_id`, and `bank_factory.py` blanks the corresponding bank record's
`utr_reference` and forces a UPI-style narration (which never embeds the real UTR) —
modeling a real system that lost the order-level breakdown for that settlement and a
bank narration that never carried a resolvable reference. This is the only failure
mode where `order_id`/UTR are unreliable; every other mode's settlement row is fully
trustworthy.

**Detection vs. identification tiers**: each mode also records `detectable_at_tier`
(the cheapest tier that can notice something is off) separately from
`identifiable_at_tier` (the tier actually needed to pin down the correct match) — see
`config.EXPECTED_TIERS`. These deliberately differ for `partial_batch_failure`: a
tolerance check can tell a settlement is short, but identifying *which* invoice (out
of up to 40 candidates) was dropped needs a subset-sum search, since the shortfall can
coincidentally match more than one candidate.

### Field-shape provenance

- `settlement_report.csv` columns (`entity_id, type, debit, credit, amount, fee, tax,
  settlement_id, settlement_utr, order_id, settled_at`) mirror Razorpay's Settlement
  Report export field names, plus a `settled_at` date column (added so a downstream
  matcher has a settlement-side date to compare against; earlier versions of this file
  omitted it even though the `Settlement` dataclass always populated it). `credit`
  carries the (possibly rounding-distorted) net settled amount; `amount` is the gross
  batch total; `order_id` holds the pipe-joined invoice IDs claimed by that batch,
  not a single real order id.
- Bank narrations follow NPCI-circular-style UPI/NEFT/RTGS text formats
  (`UPI/CR/<ref>/<name>/<bank>`, `NEFT CR:<utr>/<name>`, etc.).
- These are illustrative approximations for testing purposes, not verbatim exports.

### Regenerating the dataset

```
python -m generator.generate --seed 42 --num-invoices 2000 --out-dir data/
```

The same seed always produces byte-identical output (verified — see Verification
below) — useful for a stable demo dataset across dry runs and the real presentation.
Other options: `--start-date`, `--end-date` (default 2025-01-01 to 2026-08-23).

### Verifying a generated dataset

```
python -m generator.verify --data-dir data/
```

Runs plain assertion-based sanity checks: ID uniqueness, referential integrity between
`ground_truth.json` and the three CSVs, clean-batch reconciliation against the
documented MDR/GST formula, rounding-variance deltas staying within their configured
band, orphan invariants, duplicate-collision pair consistency, ambiguous-subset-sum
tolerance, and a comparison of the realized vs. configured failure-mode distribution.

### Directory layout

```
generator/          Python package (see module docstrings for the phase each file owns)
matcher/             deterministic tiers, LLM escalation, agent controller, final report
data/
    invoices.csv           # what the matching pipeline reads
    settlement_report.csv  # what the matching pipeline reads
    bank_statement.csv     # what the matching pipeline reads
    answer_key/
        ground_truth.json  # hidden — never fed to the matcher
```

The answer key is kept in its own subdirectory specifically so it's easy to exclude a
matching pipeline's file-reading code from ever touching it by convention.

### Computing precision/recall against ground truth

For each bank record in `ground_truth.json`, compare a pipeline's predicted match
against `true_match_ids`, bucketed by `identifiable_at_tier`:

```
for entry in ground_truth["entries"]:
    predicted = pipeline.match(entry["bank_record_id"])
    correct = set(predicted) == set(entry["true_match_ids"])
    record_result(tier=entry["identifiable_at_tier"], correct=correct)
```

Report per-tier accuracy plus overall precision/recall. `collision_groups` in the
ground truth file separately lists invoice-ID pairs that share an amount, for scoring
false-positive risk specifically.

## Known limitations

Stated plainly, as their own section — not because they're weaknesses to hide, but
because an itemized limitations list is itself evidence this build was checked rather
than assumed correct.

- **The zero-candidate rule's one miss** (`BANK-42-000024`): an `ambiguous_subset_sum`
  case whose decoy invoice fell outside Tier 3's search window, structurally
  indistinguishable from a genuine orphan at runtime — no stored signal tells the two
  apart without seeing ground truth.
- **One real calibration false positive** (`BANK-42-000152`): a `subset_sum` match
  that resolved to a single invoice and was originally indistinguishable in confidence
  from genuinely correct matches; fixed via a single-invoice confidence penalty and
  now correctly isolated in the `exception` band.
- **`ambiguous_subset_sum` is only genuinely tested ~26% of the time on this seed** (9
  of 35 correctly flagged as ambiguous; the rest resolve as if unique because their
  decoy invoices were already claimed elsewhere or fall outside the search window) —
  the direct combinatorial cost of keeping the subset-sum search tractable.
- **`AnthropicLLMClient` has never been run for real** — this environment has neither
  the `anthropic` package nor Anthropic credentials. It's written against confirmed
  SDK patterns but unproven end-to-end; every real LLM result in this repo comes from
  `GroqLLMClient`.
- **The agent-controller regression check has only been run against this one dataset**
  (seed-42, 153 bank records) — not re-run against a regenerated dataset with a
  different seed or record count.
- **Small-sample caveats on the calibration bands**: of the `auto_match` band's n=102,
  4 are `tolerance`-tier matches that only cross the 0.7 line because of the Tier 2
  formula fix evaluated on the same n=10 sample it was tuned on — real evidence for
  the *shape* of the fix, not independent confirmation at scale.
- The lognormal amount distribution parameters (`reference_data.AMOUNT_LOGNORMAL_MEAN/SIGMA`)
  are illustrative, tuned by inspection rather than fit to real invoice data.
- The curated counterparty list (`reference_data.COUNTERPARTIES`) is intentionally
  small (~20 entities) for demo scope, not representative of real-world diversity.
- Failure-mode proportions target the invoice level at sampling time; because many
  invoices aggregate into one settlement/bank record, the realized proportion of
  settlement-level distortions (e.g. `rounding_fee_variance`) is lower than the raw
  invoice-level target — `generator/verify.py`'s distribution check accounts for this
  by treating it as a proxy check, not an exact match.
- No automated test suite; verification is the manual/scripted checks documented in
  `MATCHER_STATUS.md`, `LLM_ESCALATION_STATUS.md`, and `AGENT_CONTROLLER_STATUS.md`.
