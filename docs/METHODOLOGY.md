# Methodology and validation

The business questions are defined in `QUESTIONS` before analysis: regional contribution, product category, peak year-month, customer concentration, and annual change. The dataset is historical; the pipeline can be rerun on refreshed input, but this file is not a live weekly data feed.

## Computation

Pandas parses dates with `format="%d/%m/%Y"`. Rows are order lines. Counts distinguish lines, unique orders, and unique customer IDs. Sales are aggregated using `groupby(...).sum()` without rounding input rows. Every full-population grouping reconciles to total source sales within an absolute tolerance of 0.000001 source units. Selected-month sales are a subset and are reconciled separately in the tests. Source precision remains in exported CSV/JSON; presentation uses `Decimal(str(value))` with `ROUND_HALF_UP` to two decimal places. This explicitly displays November 2018 sales of 117938.155 as $117,938.16.

Customer sales use Customer ID and verified Customer Name. Top-ten concentration is the sum of the ten highest customer totals divided by all sales. Annual growth is `(current annual sales / previous annual sales - 1) * 100`; the first year and nonconsecutive-year comparisons are unavailable. Annual comparisons reflect observed data coverage, not necessarily complete fiscal-year accounts. Ties are counted, described as joint leaders, and resolved for display using ascending labels.

### Seasonal concentration and inventory recommendation

Filter Order Date to calendar months 9, 11 and 12, sum Sales, then divide by sales across the full observed period. This yields 42.9683611442%, presented as 42.97% in the report and 43% on the resume. These months were selected in exploratory analysis; the result is descriptive, not a significance test. `calendar_month_summary.csv` exposes all twelve pooled calendar-month totals, including zero for unobserved months. `seasonality_summary.csv` shows selected-month sales divided by the matching year's total, retaining years with no selected-month sales as zero and zero-total years as unavailable.

Annual shares are 47.45% (2015), 46.35% (2016), 40.65% (2017) and 39.76% (2018), so the pooled share should not be described as a constant annual pattern. The proposed action is to plan inventory reviews and supplier lead times ahead of these months, then check current SKU demand before changing quantities. No stock increase percentage or inventory benefit is assumed.

### Customer concentration and retention recommendation

Select `ceil(0.10 * distinct customer count)` accounts from customer totals ranked by sales descending and Customer ID ascending for ties. With 793 customers this is 80 accounts, actually 10.09% of customers after rounding. Divide those accounts' sales by all sales to obtain 31.1600999104%, presented as 31.16% in the report and 31% on the resume. Ties at the cutoff do not expand the cohort. `customer_summary.csv` exports every customer's sales rank and boolean cohort flag. This is a top-decile-by-count cohort, distinct from the top ten accounts (6.80% of sales).

The recommendation is a targeted retention pilot with account check-ins and tailored outreach, subject to purchase-recency and margin review before incentives. The analysis does not infer churn risk or profit from sales. `recommendations.json` preserves both findings, proposed actions and limits; HTML displays the same content. Recommendations are analyst-authored and have no measured outcome.

## LLM boundary

Only four leader facts and approved sentence alternatives go to the local Ollama service, not transaction rows. The prompt asks for four sentences, one number per sentence, no arithmetic and no added claims. The model uses structured JSON, temperature zero and a fixed seed. Inference runs on CPU (`num_gpu=0`) because the installed accelerated runner crashed during verification; this favors portability over speed. Exact reproducibility across model/runtime versions is not guaranteed.

The year in the monthly sentence is spelled out to preserve the one-number rule without omitting the year. The HTML table and README use standard year-month notation. Narration is deliberately constrained: Llama chooses among approved sentence frames. This is a small controlled-language generation task, not open-ended analysis or a demonstration of reasoning ability.

## Publication gate

1. Parse structured JSON and require exactly four strings in fact order.
2. Extract numeric tokens from each sentence and require exactly one.
3. Compare the extracted Decimal to the Pandas result rounded for display.
4. Require the sentence to match one approved statement for that exact fact. This catches swapped labels, wrong ranks, changed currency and unsupported explanatory text.
5. Retry invalid generation at most three times, recording rejected attempts. If all fail, raise an error before writing outputs. Never silently replace failed model text with a template.

The explicit `--mode template` preview is separately labeled and does not claim model use. A failed new run leaves previous output files intact; check the run timestamp and input hash to distinguish the previous successful report. Each core text artifact is replaced atomically, but the collection is not a transactional publication bundle; do not run concurrent writers into the same output directory.

## Tests and evidence

The 37-test suite covers input schema, dates, missing values, duplicate rules, sales validity, postal codes, reconciliation, rankings, tie handling, customer identity, year gaps, rounding, prompt evidence, numeric/entity/currency mutations, HTML escaping, model retries and failures, and end-to-end artifact generation. Extended assertions cover seasonal shares, years with no selected sales, cohort sizes for 1/10/11/20 customers, deterministic cutoff ties, membership flags and recommendation exports. Optional dataset regression tests compare total sales and both resume findings to independent Decimal calculations over raw CSV rows.

CI uses synthetic data and needs no Ollama service. Model integration is verified separately through `reports/run.json`, which records the actual model response, validation checks, prompt, UTC timestamp, runtime, and input hash. A passing four-sentence sample does not establish a general model accuracy percentage. Runtime is a single observed run, not a benchmark or evidence of business time savings.

## Sources

- Dataset: user-supplied attachment; exact version identified by SHA-256 in the run artifact. No redistribution license supplied.
- [Ollama generate API](https://docs.ollama.com/api/generate): local generation endpoint and response fields.
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs): JSON-schema response format.
