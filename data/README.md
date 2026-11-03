# Input data

The file has 9,800 order lines and 18 columns, resembling a US Superstore retail sales dataset. It covers 2015-01-03 through 2018-12-30, with 4,922 distinct orders and 793 customer IDs. Do not substitute another Superstore version and expect identical results.

| Field | Interpretation |
| --- | --- |
| Row ID | Unique order-line identifier |
| Order ID | Order identifier; legitimate repeats across product lines |
| Order Date, Ship Date | Explicit day/month/year strings |
| Customer ID, Customer Name | Aggregate by ID with a name consistency check |
| Region, Category | Sales breakdowns |
| Sales | Nonnegative sales amount; source has sub-cent precision |
| Postal Code | String identifier, padded to five digits; missing values preserved |
| Remaining columns | Segment, geography, shipping, product descriptors; preserved during cleaning |

Sales are assumed to be USD for presentation because this is a US retail example. Currency is not explicitly documented in the CSV. Profit, costs, quantities, and discounts are absent; this project does not calculate them.

Missing postal codes are flagged, never guessed or filled from an ambiguous city lookup. All eleven affected lines remain in sales totals. Duplicate detection checks full rows and unique Row IDs; repeated Order IDs are valid. The input SHA-256 is recorded in `reports/run.json`.
