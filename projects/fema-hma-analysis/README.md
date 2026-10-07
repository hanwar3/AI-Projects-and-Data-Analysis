# Who gets federal hazard mitigation money, and for what?

Quantitative analysis of the OpenFEMA **Hazard Mitigation Assistance Projects (v4)** dataset, FY2015–2024.

- **Notebook:** [`hma_analysis.ipynb`](hma_analysis.ipynb) — every step with its code directly above its output
- **Results:** [`output/`](output) — figures (PNG) and result tables (CSV)

## Questions and tests

| Question | Test |
|---|---|
| Do HMGP, BRIC/PDM and FMA fund different kinds of projects? | Pearson χ² test of independence, Cramér's V, adjusted standardized residuals |
| Does award size differ by project class? | Levene, Welch ANOVA, Kruskal–Wallis, Tukey HSD on log federal share |
| What drives award size, net of other factors? | OLS on log federal share with project class, program, FEMA region and fiscal-year fixed effects; VIF, Breusch–Pagan, Jarque–Bera; HC3 and state-clustered standard errors |
| Which states receive the most, and for what? | Descriptive totals by state and class |

## Main results

Sample: 7,781 approved physical mitigation projects (planning, management costs and studies excluded), $7.83B federal share.

- Project mix depends strongly on program: χ²(10) = 1,970, p < 0.001, Cramér's V = 0.36. FMA over-funds elevations and buyouts; HMGP carries most generator and warning projects.
- Award size differs by class: Welch F = 226, p < 0.001. Median award ranges from $120k (generators and warning) to $642k (elevation).
- Holding project type, region and year constant, BRIC/PDM awards are about 96% larger than HMGP awards (95% CI +67% to +130%; p = 0.001 with state-clustered errors). R² = 0.18.
- Florida, Texas, Louisiana, California and Puerto Rico received 53% of the federal share.

## Reproduce

1. Download `HazardMitigationAssistanceProjects.csv` from the [OpenFEMA dataset page](https://www.fema.gov/openfema-data-page/hazard-mitigation-assistance-projects-v4) and save it next to the notebook as `HazardMitigationAssistanceProjects_v4.csv`.
2. `pip install pandas scipy statsmodels matplotlib jupyter`
3. Run `hma_analysis.ipynb` top to bottom.

The results here use the file downloaded on 3 October 2026; FEMA refreshes the dataset daily, so counts may shift slightly.

## Limits

These are grant records, not outcomes: they show where mitigation money went and for what, not whether it reduced later losses. Testing efficacy needs loss data (NFIP claims, IHP registrations or the National Risk Index) aggregated with this file to a common unit such as the county.
