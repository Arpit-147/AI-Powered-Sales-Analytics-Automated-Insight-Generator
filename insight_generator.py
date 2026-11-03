"""Auditable sales analysis and locally generated, validated business narratives."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from html import escape
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

QUESTIONS = [
    "Which region leads sales, and how do the other regions compare?",
    "Which product category drives the most sales?",
    "Which year-month peaks, and what share of sales falls in September, November and December?",
    "What share of sales comes from the top ten percent of customers, and which accounts are included?",
    "How do annual sales change over the observed period?",
]
REQUIRED = {"Row ID", "Order ID", "Order Date", "Ship Date", "Customer ID",
            "Customer Name", "Postal Code", "City", "State", "Region", "Category", "Sales"}
NUMBER = re.compile(r"(?<!\w)[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?(?!\w)")


class ValidationError(ValueError):
    """Input or generated output violates the reporting contract."""


def money(value: float) -> str:
    """Round only for display, explicitly using decimal half-up to cents."""
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${rounded:,.2f}"


def clean_data(path: str | Path) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(path, dtype="string")
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValidationError(f"Missing columns: {sorted(missing)}")
    if df.empty:
        raise ValidationError("Dataset is empty")
    # Check before normalization. Order IDs repeat legitimately at line-item grain.
    duplicates = int(df.duplicated().sum())
    if duplicates or df["Row ID"].duplicated().any():
        raise ValidationError("Duplicate rows or Row ID values; resolve upstream")
    for column in df.columns:
        df[column] = df[column].str.strip().replace("", pd.NA)
    if df.drop(columns="Postal Code").isna().any().any():
        raise ValidationError("Missing required non-postal data")
    for column in ["Order Date", "Ship Date"]:
        try:
            df[column] = pd.to_datetime(df[column], format="%d/%m/%Y", errors="raise")
        except ValueError as exc:
            raise ValidationError(f"Invalid day/month/year values in {column}") from exc
    if (df["Ship Date"] < df["Order Date"]).any():
        raise ValidationError("Ship Date precedes Order Date")
    try:
        df["Sales"] = pd.to_numeric(df["Sales"], errors="raise")
    except ValueError as exc:
        raise ValidationError("Sales must be numeric") from exc
    if df["Sales"].isna().any() or not df["Sales"].map(lambda x: float('-inf') < x < float('inf')).all() or (df["Sales"] < 0).any():
        raise ValidationError("Sales must be finite, nonnegative values")
    if df["Sales"].sum() <= 0:
        raise ValidationError("Total sales must be positive")
    # Postal codes are identifiers: preserve leading zeros and retain unknowns.
    missing_postal = int(df["Postal Code"].isna().sum())
    df["Postal Code Missing"] = df["Postal Code"].isna()
    postal = df["Postal Code"].dropna()
    if not postal.str.fullmatch(r"\d{1,5}").all():
        raise ValidationError("Expected US postal codes containing up to five digits")
    df["Postal Code"] = df["Postal Code"].str.zfill(5)
    df["Month"] = df["Order Date"].dt.to_period("M").astype(str)
    df["Year"] = df["Order Date"].dt.year
    audit = {"rows": len(df), "columns_raw": len(df.columns) - 3,
             "exact_duplicates": duplicates, "missing_postal_codes": missing_postal,
             "postal_policy": "Retain missing values, flag them, and include every row in sales totals; never infer a ZIP code.",
             "date_format": "%d/%m/%Y", "date_min": str(df["Order Date"].min().date()),
             "date_max": str(df["Order Date"].max().date()),
             "orders": int(df["Order ID"].nunique()), "customers": int(df["Customer ID"].nunique())}
    return df, audit


def ranked(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    # Stable tie breaking makes reruns deterministic; ties are disclosed below.
    result = df.groupby(keys, as_index=False, dropna=False)["Sales"].sum()
    return result.sort_values(["Sales", *keys], ascending=[False, *([True] * len(keys))]).reset_index(drop=True)


def calculate_metrics(df: pd.DataFrame) -> tuple[dict, dict[str, pd.DataFrame]]:
    names_per_id = df.groupby("Customer ID")["Customer Name"].nunique()
    if (names_per_id > 1).any():
        raise ValidationError("A customer ID maps to multiple names")
    total = float(df["Sales"].sum())
    tables = {"region": ranked(df, ["Region"]), "category": ranked(df, ["Category"]),
              "month": ranked(df, ["Month"]), "customer": ranked(df, ["Customer ID", "Customer Name"])}
    tables["calendar_month"] = df.groupby(df["Order Date"].dt.month.rename("Month number"), as_index=True)["Sales"].sum().reindex(range(1, 13), fill_value=0).reset_index()
    tables["calendar_month"]["Month name"] = tables["calendar_month"]["Month number"].map(lambda m: datetime(2000, m, 1).strftime("%B"))
    annual = df.groupby("Year", as_index=False)["Sales"].sum().sort_values("Year")
    annual["YoY percent"] = annual["Sales"].pct_change(fill_method=None) * 100
    # Missing years break the YoY comparison rather than implying one-year growth.
    annual.loc[annual["Year"].diff().ne(1), "YoY percent"] = float("nan")
    tables["annual"] = annual
    for table in tables.values():
        table["Sales share percent"] = table["Sales"] / total * 100
        if abs(float(table["Sales"].sum()) - total) > 0.000001:
            raise ValidationError("Grouped sales do not reconcile to source total")
    metrics = {"total_sales": total, "top_ten_customer_share_percent": float(tables["customer"].head(10)["Sales"].sum() / total * 100)}
    customer_count = len(tables["customer"])
    top_count = (customer_count + 9) // 10  # ceil(10%): include at least one account.
    tables["customer"]["Sales rank"] = range(1, customer_count + 1)
    tables["customer"]["Top ten percent cohort"] = tables["customer"]["Sales rank"] <= top_count
    top_sales = float(tables["customer"].head(top_count)["Sales"].sum())
    metrics["top_ten_percent_customers"] = {
        "customer_count": top_count, "total_customers": customer_count,
        "actual_customer_share_percent": top_count / customer_count * 100,
        "sales": top_sales, "sales_share_percent": top_sales / total * 100,
        "selection_rule": "ceil(0.10 * distinct customers), ranked by sales descending, ties by Customer ID ascending"}
    selected = df["Order Date"].dt.month.isin([9, 11, 12])
    selected_sales = float(df.loc[selected, "Sales"].sum())
    metrics["seasonal_concentration"] = {"months": [9, 11, 12],
        "month_names": ["September", "November", "December"],
        "sales": selected_sales, "sales_share_percent": selected_sales / total * 100,
        "period_start": str(df["Order Date"].min().date()), "period_end": str(df["Order Date"].max().date()),
        "scope": "Pooled share across the full observed period, not a constant annual share or forecast"}
    seasonal = df.loc[selected].groupby("Year")["Sales"].sum()
    tables["seasonality"] = annual[["Year", "Sales"]].rename(columns={"Sales": "Annual sales"}).copy()
    tables["seasonality"]["Selected-month sales"] = tables["seasonality"]["Year"].map(seasonal).fillna(0)
    denominator = tables["seasonality"]["Annual sales"].replace(0, float("nan"))
    tables["seasonality"]["Selected-month share percent"] = tables["seasonality"]["Selected-month sales"] / denominator * 100
    for dimension, key in [("region", "Region"), ("category", "Category"), ("month", "Month"), ("customer", "Customer Name")]:
        row = tables[dimension].iloc[0]
        metrics[f"top_{dimension}"] = {"label": str(row[key]), "sales": float(row["Sales"]),
            "tied_leaders": int(tables[dimension]["Sales"].eq(row["Sales"]).sum())}
    return metrics, tables


def recommendations(metrics: dict) -> list[dict]:
    """Analyst recommendations tied to measured findings, with no claimed uplift."""
    season = metrics["seasonal_concentration"]
    cohort = metrics["top_ten_percent_customers"]
    return [
        {"finding": f"September, November and December contributed {season['sales_share_percent']:.2f}% of sales across the observed period.",
         "action": "Plan inventory reviews and supplier lead times ahead of September, November and December; check recent SKU demand and stock availability before changing purchase quantities.",
         "limitation": "Historical concentration varies by year and does not establish future demand or justify a fixed stock increase."},
        {"finding": f"The top {cohort['customer_count']} of {cohort['total_customers']} customers contributed {cohort['sales_share_percent']:.2f}% of sales.",
         "action": "Use the flagged customer cohort for a retention pilot with account check-ins and tailored outreach; review purchase recency and margins before offering incentives.",
         "limitation": "Sales concentration does not prove churn risk or customer profitability; retention uplift has not been measured."},
    ]


def facts_for_narrative(metrics: dict) -> list[dict]:
    facts = []
    # Month is written in words so every sentence contains exactly one number.
    digits = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    small = digits + ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    for dimension in ["region", "category", "month", "customer"]:
        metric = metrics[f"top_{dimension}"]
        label = metric["label"]
        if dimension == "month":
            month = datetime.strptime(label, "%Y-%m")
            tail = month.year % 100
            tail_words = small[tail] if tail < 20 else tens[tail // 10] + (" " + digits[tail % 10] if tail % 10 else "")
            year_words = ("two thousand" + (" " + tail_words if tail else "")) if 2000 <= month.year < 2100 else " ".join(digits[int(c)] for c in str(month.year))
            label = month.strftime("%B") + " of " + year_words
        value = money(metric['sales'])
        rank = "a joint leader" if metric["tied_leaders"] > 1 else "the leader"
        # Controlled language rejects unsupported causal claims as well as wrong numbers.
        descriptions = {"region": "regional sales", "category": "category sales",
                        "month": "monthly sales", "customer": "customer sales"}
        description = descriptions[dimension]
        frames = [f"{label} was {rank} in {description}, with {value}.",
                  f"With {value} in sales, {label} was {rank} in {description}."]
        facts.append({"id": dimension, "label": label, "amount": value,
                      "allowed_sentences": frames})
    return facts


def build_prompt(facts: list[dict]) -> str:
    return ("Write a four-sentence business sales summary using only the verified facts below. "
            "Return JSON with a sentences array in fact order; choose exactly one allowed sentence per fact. "
            "Each sentence must contain exactly one numeric amount, copied with its dollar sign and two decimals. "
            "Do not calculate, add claims, round values, add markdown, or change names; years are deliberately spelled out.\n"
            + json.dumps(facts, ensure_ascii=False, indent=2))


def validate_narrative(sentences: list[str], facts: list[dict]) -> dict:
    if not isinstance(sentences, list) or len(sentences) != len(facts):
        raise ValidationError("Expected exactly four sentences")
    checks = []
    for sentence, fact in zip(sentences, facts):
        if not isinstance(sentence, str):
            raise ValidationError("Every sentence must be text")
        numbers = NUMBER.findall(sentence)
        expected = Decimal(fact["amount"].replace("$", "").replace(",", ""))
        if len(numbers) != 1 or Decimal(numbers[0].replace(",", "")) != expected:
            raise ValidationError(f"Numeric mismatch for {fact['id']}: {numbers}")
        if sentence not in fact["allowed_sentences"]:
            raise ValidationError(f"Unsupported entity, ranking, wording or currency for {fact['id']}")
        checks.append({"fact": fact["id"], "expected": str(expected), "observed": numbers[0], "passed": True})
    return {"passed": True, "sentences_checked": len(checks), "checks": checks,
            "scope": "Exact numeric, currency, entity, ranking and controlled-sentence checks; not unrestricted natural-language verification."}


def generate_narrative(facts: list[dict], model: str = "llama3.2:3b", timeout: int = 180, attempts: int = 3) -> tuple[list[str], dict]:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    prompt = build_prompt(facts)
    schema = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4}}, "required": ["sentences"], "additionalProperties": False}
    history = []
    for attempt in range(1, attempts + 1):
        payload = {"model": model, "prompt": prompt, "stream": False, "format": schema,
                   "options": {"temperature": 0, "seed": 42, "num_predict": 512, "num_ctx": 4096, "num_gpu": 0}}
        request = Request("http://127.0.0.1:11434/api/generate", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cannot reach local Ollama. Start `ollama serve` and run `ollama pull llama3.2:3b`.") from exc
        try:
            if result.get("done") is not True or result.get("done_reason") == "length":
                raise ValidationError("Model response is incomplete")
            sentences = json.loads(result["response"])["sentences"]
            checks = validate_narrative(sentences, facts)
        except (ValueError, KeyError, TypeError) as exc:
            history.append({"attempt": attempt, "response": result.get("response"), "error": str(exc)})
            prompt = build_prompt(facts) + "\nThe previous response failed validation. Copy the allowed sentences exactly."
            continue
        return sentences, {"source": "Ollama local API", "model": result.get("model", model),
            "created_at": result.get("created_at"), "attempts": attempt, "rejected_attempts": history,
            "raw_response": result["response"], "prompt": payload["prompt"], "validation": checks,
            "total_duration_ns": result.get("total_duration"), "eval_count": result.get("eval_count")}
    raise ValidationError(f"All {attempts} model responses failed; report not published. {history}")


def render_html(audit: dict, metrics: dict, tables: dict, sentences: list[str], source: str) -> str:
    sections = []
    for name, table in tables.items():
        view = table.head(10).copy() if name == "customer" else table.copy()
        if name == "month":
            view = view.sort_values("Month")
        for column in ["Sales", "Sales share percent", "YoY percent", "Annual sales", "Selected-month sales", "Selected-month share percent"]:
            if column in view:
                view[column] = view[column].map(lambda v: "Unavailable" if pd.isna(v) else (money(v) if column in {"Sales", "Annual sales", "Selected-month sales"} else f"{v:,.2f}%"))
        title = {"region": "Sales by region", "category": "Sales by category", "month": "Monthly sales", "customer": "Top customers by sales (first ten shown; full cohort in customer_summary.csv)", "annual": "Annual sales", "calendar_month": "Calendar-month sales pooled across years", "seasonality": "September, November and December share by year"}[name]
        sections.append(f"<section><h2>{title}</h2><div class='table-wrap'>{view.to_html(index=False, border=0, escape=True)}</div></section>")
    narrative = "".join(f"<p>{escape(sentence)}</p>" for sentence in sentences)
    questions = "".join(f"<li>{escape(q)}</li>" for q in QUESTIONS)
    season = metrics["seasonal_concentration"]
    cohort = metrics["top_ten_percent_customers"]
    actions = "".join(f"<h3>{escape(item['finding'])}</h3><p>{escape(item['action'])}</p><p class='muted'>{escape(item['limitation'])}</p>" for item in recommendations(metrics))
    bars = "".join(f"<div class='bar-row'><span>{escape(str(r.Region))}</span><meter min='0' max='{metrics['total_sales']}' value='{r.Sales}'></meter><strong>{money(r.Sales)}</strong></div>" for r in tables["region"].itertuples())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Retail sales | Automated Insight Generator</title>
<style>body{{margin:0;background:#f2f5fa;color:#17253b;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:48px 24px}}header{{border-top:5px solid #16a085;padding:28px 0}}h1{{font-size:42px;line-height:1.15;margin:8px 0}}.eyebrow{{letter-spacing:2px;color:#32677f;font-size:13px;text-transform:uppercase}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}}.card,section{{background:white;border:1px solid #dce3ee;border-radius:12px;padding:24px;margin:20px 0}}.card strong{{display:block;font-size:28px}}h2{{font-size:23px;margin-top:0}}.muted,footer{{color:#52647a;font-size:14px}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px 14px;border-bottom:1px solid #e7ecf3;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#edf3f8}}.bar-row{{display:grid;grid-template-columns:80px 1fr 140px;gap:12px;align-items:center;margin:16px 0}}meter{{width:100%;height:24px}}.bar-row strong{{text-align:right}}@media(max-width:550px){{h1{{font-size:32px}}.bar-row{{grid-template-columns:60px 1fr}}.bar-row strong{{grid-column:2}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
<header><div class="eyebrow">Retail sales analysis</div><h1>Automated Insight Generator</h1><p>{audit['date_min']} to {audit['date_max']} · Historical order-line sales</p></header>
<div class="cards"><div class="card">Total sales<strong>{money(metrics['total_sales'])}</strong></div><div class="card">Order lines<strong>{audit['rows']:,}</strong></div><div class="card">Distinct orders<strong>{audit['orders']:,}</strong></div><div class="card">Customers<strong>{audit['customers']:,}</strong></div></div>
<section><h2>Business questions</h2><ol>{questions}</ol></section>
<section><h2>Key findings and recommendations</h2><p>September, November and December contributed <strong>{season['sales_share_percent']:.2f}%</strong> of all observed sales. The top ten percent of customers (rounded up to <strong>{cohort['customer_count']} of {cohort['total_customers']}</strong>) contributed <strong>{cohort['sales_share_percent']:.2f}%</strong>.</p>{actions}<p class="muted">Recommendations are analyst proposals supported by historical findings; no inventory or retention outcome has been measured.</p></section>
<section><h2>From numbers to narrative</h2><p class="muted">{escape(source)}. All four sentences passed numeric and controlled-language validation.</p>{narrative}</section>
<section><h2>Regional contribution</h2>{bars}</section>
<section><h2>Customer concentration</h2><p>The top ten percent cohort contributes {cohort['sales_share_percent']:.2f}% of sales ({money(cohort['sales'])}). It includes {cohort['customer_count']} customers, or {cohort['actual_customer_share_percent']:.2f}% of the customer base after rounding the cohort count up. By comparison, the ten highest-sales customers contribute {metrics['top_ten_customer_share_percent']:.2f}%. Customer groups use stable IDs; sales rank and cohort membership are exported for every customer.</p></section>
{''.join(sections)}<section><h2>Data quality and interpretation</h2><p>{audit['missing_postal_codes']} missing postal codes retained and flagged; {audit['exact_duplicates']} exact duplicates. Dates parsed explicitly as day/month/year. Every grouped total reconciles to source sales.</p><p>Sales are treated as USD for this US retail portfolio example; the CSV does not explicitly document currency. No profit, costs, quantities or targets are provided. Rankings describe observed sales, not profitability, causes, or a forecast. Annual changes compare observed calendar years and may reflect coverage differences. Unknown postal codes are never invented.</p></section>
<footer>See metrics.json, run.json, prompt.txt and summary tables for audit evidence. Amounts retain source precision in exports and use decimal half-up rounding to cents for display.</footer></main></body></html>"""


def run_report(csv_path: str | Path = "data/train.csv", output_dir: str | Path = "reports", *, model: str = "llama3.2:3b", mode: str = "ollama") -> Path:
    """Run cleaning → Pandas metrics → model → validation → HTML and audit artifacts."""
    started = time.perf_counter()
    if mode not in {"ollama", "template"}:
        raise ValueError("mode must be ollama or template")
    frame, audit = clean_data(csv_path)
    metrics, tables = calculate_metrics(frame)
    facts = facts_for_narrative(metrics)
    if mode == "ollama":
        sentences, generation = generate_narrative(facts, model=model)
    else:
        sentences = [fact["allowed_sentences"][0] for fact in facts]
        generation = {"source": "Deterministic template preview (no LLM used)", "model": None,
                      "validation": validate_narrative(sentences, facts), "prompt": build_prompt(facts)}
    report = render_html(audit, metrics, tables, sentences, generation["source"])
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    run = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
           "input_sha256": hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
           "pandas_version": pd.__version__, "mode": mode, "quality": audit,
           "generation": generation, "runtime_seconds": time.perf_counter() - started}
    artifacts = {"metrics.json": json.dumps(metrics, indent=2, allow_nan=False),
                 "recommendations.json": json.dumps(recommendations(metrics), indent=2, allow_nan=False),
                 "run.json": json.dumps(run, indent=2, allow_nan=False),
                 "prompt.txt": generation["prompt"], "narrative.txt": "\n".join(sentences) + "\n",
                 "report.html": report}
    for name, content in artifacts.items():
        target = output / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    for name, table in tables.items():
        table.to_csv(output / f"{name}_summary.csv", index=False)
    return output / "report.html"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/train.csv")
    parser.add_argument("--output", default="reports")
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--mode", choices=["ollama", "template"], default="ollama")
    args = parser.parse_args()
    try:
        print(run_report(args.input, args.output, model=args.model, mode=args.mode))
    except (ValidationError, RuntimeError) as error:
        parser.exit(1, f"Report failed: {error}\n")
