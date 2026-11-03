# Resume and interview notes

## ATS-friendly project entry

**Automated Retail Sales Insight Generator | Python, Pandas, Llama 3.2, Ollama, GitHub Actions**

- Analyzed 9,800 order line items ($2.26M, 2015–2018) using Python/Pandas; found September, November, and December contributed 43% of sales, supporting seasonal inventory planning.
- Found the top 10% of customers contributed 31% of sales, supporting targeted retention recommendations.
- Automated HTML reports and KPI narratives with Llama 3.2 via Ollama, validating generated figures, entities, and rankings; implemented 37 automated tests across cleaning, reconciliation, and reporting.

The percentages are rounded from 42.9684% and 31.1601%. The seasonal share covers the entire observed period; annual shares differ. The customer cohort contains 80 of 793 customers, using the ceiling of 10% and deterministic sales/ID ranking. Both findings, the flagged customer cohort, and the inventory/retention recommendations are implemented in the pipeline and saved outputs. Recommendations have not been deployed and no uplift is claimed.

Use only claims you can explain and reproduce. Do not claim revenue uplift, production deployment, reporting hours saved, a percentage reduction in manual work, or universal LLM accuracy: those outcomes were not measured. Add this repository's URL after you create it on GitHub. The source currency is a documented USD assumption.

## Interview answers

**What business problem does this solve?** Repeatedly turning sales summaries into a short, consistent written report. The analyst defines the questions and interprets implications; Pandas calculates the metrics and the local model performs constrained narration.

**How do you stop fabricated numbers?** The model receives only precomputed facts. The validator extracts each numeric token, compares it to the rounded source metric using Decimal, and checks the exact approved entity/ranking sentence. Failed outputs are retried and then rejected before publication.

**Why not just check that all numbers appear somewhere in a whitelist?** A model can attach a valid number to the wrong region. Validation checks one fact per sentence in a defined order, plus entity and relationship. Extra claims and numbers fail too.

**Why use an LLM if templates already work?** For four rigid facts, deterministic templates are simpler and sufficient. This portfolio deliberately demonstrates the integration and validation boundary; the LLM adds limited value under such tight constraints. A future extension could allow richer language, but would need stronger semantic evaluation before relaxing the gate.

**How did you clean postal codes?** Kept them as strings to preserve leading zeros, flagged the missing values, and retained all affected transactions. Inferring a ZIP from city and state can be ambiguous and was unnecessary for these questions.

**Which findings matter?** September, November and December contributed 42.97% of sales across the observed period, supporting inventory reviews and supplier planning ahead of these months. The 80 highest-sales customers (the top 10% rounded up) contributed 31.16%, supporting a targeted retention pilot. These are historical descriptive findings; annual concentration varies, and sales alone do not prove churn risk, profitability or future behavior. The yearly seasonal table and customer cohort flags make the recommendations traceable.

**What would you improve next?** Add a verified data source and license, dated ingestion snapshots, a measured human-reporting baseline, and a representative model evaluation set. Validate richer claims before enabling unconstrained text. Compare full-year coverage before operational forecasting.

## GitHub publication

Create an empty repository named `automated-retail-insight-generator`, then use GitHub Desktop to add this existing folder, review the changes, commit and publish. Alternatively, use `git add .`, `git commit -m "Build validated retail sales insight pipeline"`, add your own remote URL, and push the current branch. No remote was configured or published as part of this local build.

The raw CSV is intentionally excluded by `.gitignore`; a fresh clone can run the synthetic tests, and a user with the original file can reproduce the report by placing it in `data/train.csv`. Verify redistribution rights before adding raw data. The committed sample report includes customer labels from the supplied dataset; review those labels before publishing if this is private business data rather than a public sample.
