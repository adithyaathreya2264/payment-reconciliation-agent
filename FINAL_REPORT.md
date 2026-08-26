# Bank Reconciliation Matcher — Final Report

Generated 2026-08-24 15:22 UTC from matcher_output (+ matcher_output_agent where available), graded against data\answer_key\ground_truth.json.

## Headline

- **153 bank records processed.**
- **144/153 (94.1%) resolved with zero AI cost** — 109 deterministic tier matches (exact/tolerance/subset-sum) + 35 rule-based exception calls (zero-candidate orphan rule).
- **Only 9 records (5.9%) ever reached a real LLM call** — 8 correct, 1 honest defer, 0 wrong (accuracy 88.9%).
- **End-to-end: 151/153 (98.7%) correct or honestly deferred** — 2 documented, known-limitation error(s) across the whole run (see Known Limitations).

## Throughput

- Deterministic tiers (1-3) over 153 records: **0.0119s wall-clock, 12899.0 records/sec** (fresh timed run, `llm_client=None`, no API cost).

| Resolution path | n | % of total |
|---|---|---|
| Tier 1 — exact | 73 | 47.7% |
| Tier 2 — tolerance | 10 | 6.5% |
| Tier 3 — subset-sum | 26 | 17.0% |
| Zero-candidate rule (deterministic, no AI) | 35 | 22.9% |
| **Real LLM call** | **9** | **5.9%** |
| **Total** | **153** | **100.0%** |

- LLM-only wall-clock (real Groq run, from `llm_report.json`): 43046.5ms total, 4782.9ms/case mean. **Not from the same run as the 12,899 records/sec figure above** — that measures the deterministic tiers with `llm_client=None`; this measures a separate real-network Groq call. They are not comparable and should not be read as contradicting each other.

## Measured Accuracy

| Path | n | precision | recall | mean confidence |
|---|---|---|---|---|
| Tier — exact | 73 | 1.0 | 1.0 | 1.0 |
| Tier — tolerance | 10 | 1.0 | 1.0 | 0.658 |
| Tier — subset_sum | 26 | None¹ | None¹ | 0.812 |
| Zero-candidate rule (`origin=rule`) | 35 | — | — | accuracy 97.1% (34/35) |
| LLM call (`origin=llm`) | 9 | — | — | accuracy 88.9% (8/9) |

¹ subset_sum's own precision/recall is 0/null because ground truth expects most subset_sum-tier cases to resolve via `llm_escalation` (genuine ambiguity), not `subset_sum` itself — the real subset-sum signal is the `ambiguous_subset_sum` population: 35 total (verified directly against `ground_truth.json`) = 9 correctly flagged as ambiguous (escalated to `tier3_ambiguous`) + 25 resolved anyway with correct invoice ids + 1 misrouted to `tier3_no_candidates` (the zero-candidate rule miss, `BANK-42-000024`, counted in Known Limitations below). The 25 "resolved anyway" is a documented processing-order artifact, hand-verified on a sample: their decoy invoices had already been claimed by unrelated Tier 1 (`exact`) settlements — the exact tier runs as a full pass over all 153 records before the subset_sum tier ever builds its candidate pools — removing the decoy from the pool before the ambiguity could arise. Not a matcher defect.

### Confidence calibration (reliability table)

| Confidence bucket | n | correct | empirical accuracy | mean confidence | calibration gap |
|---|---|---|---|---|---|
| [0.0, 0.45) | 1 | 0 | 0.0 | 0.402 | 0.402 |
| [0.45, 0.7) | 6 | 6 | 1.0 | 0.542 | -0.458 |
| [0.7, 0.9) | 29 | 29 | 1.0 | 0.829 | -0.171 |
| [0.9, 1.0] | 73 | 73 | 1.0 | 1.0 | 0.0 |

**Auto-match / review / exception bands** (`config.py` thresholds):

| Band | n | correct | empirical accuracy |
|---|---|---|---|
| auto_match | 102 | 102 | 1.0 |
| needs_review | 6 | 6 | 1.0 |
| exception | 1 | 0 | 0.0 |

## Honest Exception List

Every unresolved/deferred record: its stage, why it escalated, and — where ground truth is available — whether it was a correct defer or a genuine miss.

| bank_record_id | stage | origin | outcome | reason |
|---|---|---|---|---|
| BANK-42-000001 | tier3_ambiguous | llm | correct_match | The sum of these five invoices equals the bank amount exactly (36253.54) and includes the Zomato Ltd invoice referenced in the narration, providing a clear match over the other near‑matches. |
| BANK-42-000004 | tier3_ambiguous | llm | correct_match | This subset sums exactly to the bank amount (96541.39) and includes the BigBasket invoice mentioned in the narration, providing a unique precise match among the candidates. |
| BANK-42-000005 | tier3_ambiguous | llm | correct_match | This subset exactly matches the bank amount (23111.25) and includes a Swiggy Bundl Technologies invoice, aligning with the UPI narration, making it the most plausible combination. |
| BANK-42-000009 | tier3_ambiguous | llm | correct_match | Subset 2 matches the bank amount exactly (57420.19) and includes Nykaa invoices referenced in the narration, making it the most plausible combination. |
| BANK-42-000012 | tier3_ambiguous | llm | correct_match | The candidate subset exactly equals the bank amount (27546.35) and includes the Swiggy Bundl Technologies invoice referenced in the narration, while the alternative subset is off by 13.92, making the exact‑match subset the more plausible explanation. |
| BANK-42-000022 | tier3_ambiguous | llm | correct_match | The second candidate subset sums exactly to the bank amount (72114.16) and includes the invoice from Priya Menon Consulting, which appears in the bank narration, providing strong supporting evidence. |
| BANK-42-000024 | tier3_no_candidates | rule | incorrect_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000027 | tier3_ambiguous | llm | correct_match | Subset 2 matches the bank amount exactly (27064.15) while subset 1 is off by 15.41; exact amount and inclusion of the narrated counterparty make this the most plausible combination. |
| BANK-42-000028 | tier3_ambiguous | llm | correct_match | The narration explicitly references Razorpay Software, which appears only in the second subset; this subset matches the bank amount exactly (48705.66) whereas the alternative is off by 2.63, making the second set the most plausible match. |
| BANK-42-000031 | tier3_ambiguous | llm | honest_defer_match_case | Multiple candidate invoice combinations sum within a small tolerance, but the narration specifically mentions Priya Menon Consulting, making it unclear whether a multi‑counterparty batch or a single‑counterparty payment is intended; no subset can be confidently selected. |
| BANK-42-000119 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000120 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000121 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000122 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000123 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000124 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000125 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000126 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000127 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000128 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000129 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000130 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000131 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000132 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000133 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000134 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000135 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000136 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000137 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000138 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000139 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000140 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000141 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000142 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000143 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000144 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000145 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000146 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000147 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000148 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000149 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000150 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000151 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |
| BANK-42-000153 | tier3_no_candidates | rule | correct_no_match | No combination of open invoices in the search window sums to this amount within tolerance -- classified as likely orphan payment. |

### Example full decision trace — `BANK-42-000001`

1. **exact → tolerance**: no exact UTR+amount+settlement-lag match found
1. **tolerance → subset_sum**: no candidate within tolerance band
1. **subset_sum → llm_escalation**: 4 minimal subsets sum within tolerance
1. **llm_escalation → resolved**: The sum of these five invoices equals the bank amount exactly (36253.54) and includes the Zomato Ltd invoice referenced in the narration, providing a clear match over the other near‑matches.

Final stage: `resolved`

## Known Limitations

These are stated as findings, not buried caveats. The first two are the 2 documented error(s) counted against the headline's 151/153 figure above — from two different populations: #1 (`BANK-42-000024`) is a `tier3_no_candidates` rule miss and does appear in the Honest Exception List table above; #2 (`BANK-42-000152`) is a `subset_sum`-tier calibration miss, resolved outside the escalation path entirely, so it does **not** appear in that table.

1. The zero-candidate orphan rule's one miss (`BANK-42-000024`, `tier3_no_candidates`): an `ambiguous_subset_sum` case whose decoy invoice fell outside Tier 3's search window, structurally indistinguishable from a genuine orphan at runtime — there is no stored signal that tells the two apart without seeing ground truth. *(source: `LLM_ESCALATION_STATUS.md`)*
2. One real calibration false positive (`BANK-42-000152`, `subset_sum`): originally scored 0.803 confidence, indistinguishable from genuinely-correct Tier 3 matches, because it resolved to a single-invoice subset while every correct match used 2+ invoices. Fixed via `config.TIER3_SINGLE_INVOICE_PENALTY`; post-fix confidence is 0.402, cleanly separated into the `exception` band below `auto_match`. *(source: `MATCHER_STATUS.md`)*
3. `AnthropicLLMClient` has never been run for real — this environment has neither the `anthropic` package installed nor Anthropic credentials. It is written against confirmed SDK patterns but is unproven end-to-end; all real LLM results in this report come from `GroqLLMClient` (`openai/gpt-oss-120b`). *(source: `LLM_ESCALATION_STATUS.md`)*
4. The agent-controller regression check (`AgentController` vs. `orchestrator.run()`) has only been run against this one real dataset (seed-42, 153 bank records) — not re-run against a regenerated dataset with a different seed or record count. *(source: `AGENT_CONTROLLER_STATUS.md`)*
