"""Tolerance bands and search bounds. Every constant is commented with the
generator/config.py constant it mirrors, so the "testing like-for-like" claim is
reviewable rather than asserted."""

from __future__ import annotations

# --- Tier 1: exact match ---
TIER1_AMOUNT_TOLERANCE = 0.01  # float-equality slack (paisa rounding), not a real tolerance band
TIER1_MAX_NORMAL_LAG_DAYS = 2  # settlement_lag_days must be <= this (mirrors generator's normal T+1/T+2 lag, weighted {1:0.6, 2:0.4})

# --- Tier 2: tolerance match --- mirrors generator.config.ROUNDING_VARIANCE_*/LATE_SETTLEMENT_EXTRA_DAYS_*
TIER2_AMOUNT_TOLERANCE_MIN = 1.0  # mirrors ROUNDING_VARIANCE_MIN
TIER2_AMOUNT_TOLERANCE_MAX = 50.0  # mirrors ROUNDING_VARIANCE_MAX
TIER2_AMOUNT_EXACT_ISH = 0.50  # near-exact threshold on amount (below the smallest real distortion, ROUNDING_VARIANCE_MIN=1.0)
TIER2_MAX_LATE_LAG_DAYS = 18  # TIER1_MAX_NORMAL_LAG_DAYS(2) + LATE_SETTLEMENT_EXTRA_DAYS_MAX(10) + worst-case weekend nudge(6)
TIER2_LAG_EXACT_ISH_DAYS = 2  # same as TIER1_MAX_NORMAL_LAG_DAYS -- near-exact on the lag dimension

# Confidence normalization floor for Tier 2's lag dimension. A genuine late_settlement
# case can never produce a lag overshoot near 0: the generator's own
# LATE_SETTLEMENT_EXTRA_DAYS_MIN(5) means the smallest achievable settlement_lag_days
# for any late case is (min normal lag, 1) + 5 = 6, i.e. overshoot >= 4, never 0-3.
# Normalizing tier2_confidence's lag dimension from an unreachable floor of 0
# compressed every real late_settlement case into the bottom quarter of the
# confidence scale (empirically: 10/10 correct matches on the seed-42 dataset scored
# only 0.5-0.625, no matter how "barely late" vs "very late" they were). See
# scoring.py::_normalize_lag and MATCHER_STATUS.md's calibration section.
TIER2_LAG_REALISTIC_MIN_OVERSHOOT_DAYS = 4  # (1 + LATE_SETTLEMENT_EXTRA_DAYS_MIN(5)) - TIER1_MAX_NORMAL_LAG_DAYS(2)

# --- Both tiers: fixed sanity band on bank-posting lag (not loosened between tiers -- it's
# structurally always small regardless of clean vs. late, per generator.config.BANK_POSTING_LAG_MAX) ---
POSTING_LAG_MAX_DAYS = 2  # mirrors generator.config.BANK_POSTING_LAG_MAX

# --- Tier 3: subset-sum search --- mirrors generator.config.MIN_BATCH_SIZE/MAX_BATCH_SIZE
#
# Pool = invoices not yet claimed by any resolved settlement, within
# TIER3_DATE_WINDOW_DAYS before bank.date. This runs for both orphan_payment (no
# settlement/UTR exists at all) and ambiguous_subset_sum (a settlement exists but its
# order_id is blanked, so Tier 1/2 can never confirm its identity -- see
# tier_exact.py's compute_settlement_lag_days docstring).
#
# Two alternatives were tried and reverted during development, both worse in
# practice despite looking more "correct" on paper:
#   - Full invoice universe (not filtered by match status), to keep an
#     ambiguous_subset_sum decoy visible even after its own real settlement claims it
#     via trusted order_id: this raised typical pool sizes into the 70-100+ range,
#     which blew past what the exhaustive subset-sum search can handle (see
#     _MAX_DP_SUBSETS below) for the large majority of cases -- net regression versus
#     the shrinking pool (many more tier3_pool_too_large escalations, far fewer
#     resolutions of any kind, including for orphan_payment).
#   - A symmetric (not backward-only) window: correct in principle (a decoy has no
#     reason to date before bank.date specifically), but combined with the full
#     universe it made pool sizes even larger. Kept backward-only for now, which
#     structurally still misses decoys dated after bank.date -- an accepted
#     limitation alongside the one below, both real and reported rather than hidden.
# The remaining accepted tradeoff: an ambiguous_subset_sum case whose decoy invoices
# were already claimed by their own legitimate settlement before Tier 3 runs (the
# normal case, since Tier 1 runs first across all bank records), or whose decoy dates
# outside this window, resolves as if unique -- see grade.py's
# ambiguous_incorrectly_resolved output. Given the algorithmic scaling limits above,
# this is a genuine, documented limitation of the deterministic tiers, not a bug to
# chase further here -- it is exactly the kind of case Tier 3's escalation path
# exists to hand to a future fuzzy/LLM stage instead of guessing.
TIER3_DATE_WINDOW_DAYS = 14
TIER3_MAX_POOL_SIZE = 40  # exceeding it escalates as "tier3_pool_too_large"; the real combinatorial guard is _MAX_DP_SUBSETS
TIER3_AMOUNT_TOLERANCE = 50.0  # same ceiling as TIER2_AMOUNT_TOLERANCE_MAX
TIER3_MAX_SUBSET_SIZE = 40  # mirrors MAX_BATCH_SIZE, capped in practice by TIER3_MAX_POOL_SIZE
SUBSET_SUM_CENTS_SCALE = 100  # amounts * 100, rounded to int, for integer DP

# --- Confidence ---
TIER1_CONFIDENCE = 1.0
TIER3_BASE_CONFIDENCE = 0.85  # ceiling for a uniquely-resolved subset-sum match -- never 1.0
# Multiplier applied when a Tier 3 match resolves to a SINGLE invoice. Chosen so a
# size-1 match's confidence drops below the observed floor of genuine multi-invoice
# matches (0.70 on the seed-42 dataset) rather than overlapping their range -- see
# scoring.py::tier3_confidence for the reasoning and calibration evidence.
TIER3_SINGLE_INVOICE_PENALTY = 0.5

# --- Auto-match / review / exception threshold bands ---
# Derived from matcher_output/calibration_report.json on the seed-42 dataset (109
# resolved matches, 1 confirmed error) -- see MATCHER_STATUS.md for the full
# reliability table and the sample-size caveats behind these numbers. Revisit if the
# dataset grows past this one seed, or once a fuzzy/LLM stage produces more graded
# examples to check against.
AUTO_MATCH_MIN_CONFIDENCE = 0.7
# >=0.7: 100% empirical accuracy, but support is NOT uniform -- 73 exact@1.0 + 25
# subset_sum in [0.7,0.85] (n=98) come from tier1/tier3_confidence, untouched this
# step, independent evidence. The remaining tolerance-tier matches that cross into
# this band only do so because of the same-session Tier 2 refit, evaluated on the
# identical small sample it was fit on -- see MATCHER_STATUS.md for the exact split.
REVIEW_QUEUE_MIN_CONFIDENCE = 0.45
# [0.45, 0.7): tolerance-tier matches with a real but small sample of empirical
# support, kept as review rather than auto-match since a small-sample perfect record
# isn't strong evidence at scale, and (like above) these values only exist via the
# same-session Tier 2 refit. Below 0.45 -> exception: this is where the one confirmed
# false positive (0.402) lives, with zero correct examples below this line.

# --- Deterministic zero-candidate orphan rule --- a tier3_no_candidates escalation
# means Tier 3's own subset-sum search already concluded, exhaustively, that nothing
# in the bounded pool explains the amount. That's a real, computed result already
# stored on the EscalationRecord -- not a guess -- so classifying it as "no_match"
# needs no LLM call. See tier_subset_sum.py::classify_zero_candidate_orphan.
# Empirically validated against ground truth on the seed-42 dataset: 34/35 (97.1%)
# tier3_no_candidates cases are correctly "no_match" this way; the sole miss is a
# known, already-documented case (an ambiguous_subset_sum whose decoy fell outside
# Tier 3's search window -- see MATCHER_STATUS.md) that looks structurally identical
# to a genuine orphan at runtime (there is no observable signal to distinguish it
# without seeing ground truth, so this is a real, accepted limitation of the rule,
# not a bug). Confidence is set below the observed 97.1% rate -- absence of a found
# subset is strong evidence but not a logical guarantee of no match, exactly as that
# one miss demonstrates, and n=35 is a modest sample.
ORPHAN_RULE_CONFIDENCE = 0.9

# --- Tier 4: LLM escalation --- handles the residual the deterministic tiers AND the
# zero-candidate orphan rule above correctly decline to guess on (tier3_ambiguous, plus
# any tier3_no_candidates/tier3_pool_too_large case the rule doesn't cover). See
# matcher/llm_tier.py.
LLM_MODEL = "claude-opus-5"  # project default per the claude-api skill; not downgraded without being told
LLM_MAX_TURNS = 4  # bounds cost/latency: at most 3 optional check_counterparty_pattern turns before submit_decision is forced
LLM_MAX_TOKENS = 4096  # decision + reason is short; no need for a large cap
LLM_INPUT_COST_PER_MTOK = 5.0
LLM_OUTPUT_COST_PER_MTOK = 25.0

# --- Groq (openai/gpt-oss-120b) --- an alternative LLMClient for the same tier,
# kept fully separate from the Anthropic constants above rather than overwriting
# them, so either provider can be selected without touching the other's config.
GROQ_MODEL = "openai/gpt-oss-120b"
# Confirmed live against console.groq.com/docs/rate-limits (2026-08-24): the free/dev
# tier TPM ceiling for openai/gpt-oss-120b is 8,000. Configured below that to leave
# headroom for the pre-call estimate's imprecision -- see
# llm_client.py::_TokenPerMinuteThrottle. If the real account limit differs from this
# (e.g. a paid tier), update this constant from the console, not from this comment.
GROQ_TPM_LIMIT = 7000
# The account's REAL hard ceiling (confirmed live, same source as above) -- distinct
# from GROQ_TPM_LIMIT above. A single request larger than this can never be sent no
# matter how empty the rate-limit window is (confirmed via a real 413 from Groq: "TPM
# Limit 8000, Requested 9219" on a 10-candidate-subset case before the deduped-pool
# context fix in llm_tier.py::build_context). GroqLLMClient checks estimates against
# THIS constant to raise ContextTooLargeError before ever attempting the call, rather
# than against GROQ_TPM_LIMIT's conservative throttle target.
GROQ_ACCOUNT_TPM_LIMIT = 8000
# Confirmed live via web search against Groq's current on-demand pricing (2026-08-24):
# $0.15 input / $0.60 output per million tokens for openai/gpt-oss-120b -- verify
# against console.groq.com's pricing page directly if this is ever load-bearing for a
# real cost claim, since search-result pricing can lag the console.
GROQ_INPUT_COST_PER_MTOK = 0.15
GROQ_OUTPUT_COST_PER_MTOK = 0.60
