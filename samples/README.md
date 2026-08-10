# Sample reports

These PDFs show what `trividia-truemetrix-report` produces for the main
`[report]` config combinations, so you can see what a layout looks like
before setting it up. Rendered from a 14-reading fixture dataset spanning
several days, including one high and one low reading.

**[`single/`](single/) is the realistic case**: a report for one patient
(Alice), the kind you'd actually hand to a doctor. **[`combined/`](combined/)
is a secondary household convenience** -- both meters in one PDF -- not
the primary use case.

Every `single/` sample has `report.include_sliding_scale = yes` and
`report.include_time_in_range = yes`, so all of them show this header, in
order:

- **Patient** / **Email** / **Meter** / **Notes** -- Meter (one
  `Meter: <device_id> (<model>)` line per distinct meter in the report)
  sits right under Email. Patient/Meter are unconditional whenever
  `--profile` is given (Meter shows even without a profile); Email/Notes
  only appear if set. See
  [config-example.ini](../config/trividia-truemetrix-daemon.ini.example)'s
  `[profile.Alice]` for the `name`/`email`/`notes` values behind these.
- A **Time in Range** pie chart: below/in-range/above counts and
  percentages, using the 70-180 mg/dL default target band (Alice has no
  `tir_low_mg_dl`/`tir_high_mg_dl` override configured in this fixture).
- A **Sliding Scale (Reference) table**: every configured band listed
  once, separate from the per-reading Dose/Note columns below.

Device ID, Model, **and Profile** deliberately live only in that header
now, not as per-row table columns in the `full` layout -- with Dose/Note
columns already in the mix, repeating a long device_id (or a name already
stated once in "Patient: ...") on every row was pushing the table off the
page edge. `report.include_device_id`/`include_model`/`include_profile`
still work, but only for `--format csv`, which has no page-width
constraint (and no header to fall back on) forcing the same tradeoff.

## single/ -- the complete layout x unit x date format matrix

All 3 layouts x 2 units x 2 date formats = 12 files.

| Layout | mg/dL, world | mg/dL, us | mmol/L, world | mmol/L, us |
|---|---|---|---|---|
| full | [full-mgdl-world.pdf](single/full-mgdl-world.pdf) | [full-mgdl-us.pdf](single/full-mgdl-us.pdf) | [full-mmol-world.pdf](single/full-mmol-world.pdf) | [full-mmol-us.pdf](single/full-mmol-us.pdf) |
| simple | [simple-mgdl-world.pdf](single/simple-mgdl-world.pdf) | [simple-mgdl-us.pdf](single/simple-mgdl-us.pdf) | [simple-mmol-world.pdf](single/simple-mmol-world.pdf) | [simple-mmol-us.pdf](single/simple-mmol-us.pdf) |
| chart | [chart-mgdl-world.pdf](single/chart-mgdl-world.pdf) | [chart-mgdl-us.pdf](single/chart-mgdl-us.pdf) | [chart-mmol-world.pdf](single/chart-mmol-world.pdf) | [chart-mmol-us.pdf](single/chart-mmol-us.pdf) |

The patient header, Time in Range chart, and Sliding Scale (Reference)
table appear in every file above -- all three are layout-independent.
Only the `full` layout additionally has per-reading Dose/Note columns
(and, for a report spanning more than one owner, `include_profile`);
`simple` is date/glucose only in side-by-side column pairs, and `chart` is
a line chart of glucose over time. `us` vs. `world` changes both the
Date/Time column/axis-label format and which date components are more
prominent (`%m/%d` axis labels vs. `%d/%m`).

The fixture includes one reading that falls in the "call doctor" band
(301+) and one below range ("do not dose"), so both show up in the `full`
layout's per-reading columns.

**This is a display lookup of a table Alice's own doctor already
prescribed -- it is not computed, validated, or recommended by this
tool.** See the main README's [Reports](../README.md#reports) section.

## combined/ (secondary, optional)

Both meters, `--multi-meter` mode -- one PDF, one section per meter. Each
section's heading always identifies its meter (`Meter: <device_id>
(<model>) — <profile>`), and that section's Email/Notes (if the owning
profile has them) always follow -- unconditional, same as `single/`'s
header, regardless of `include_sliding_scale`.

| File | Shows |
|---|---|
| [multi-meter-mgdl-world.pdf](combined/multi-meter-mgdl-world.pdf) | Both Alice's and Bob's meters, `include_sliding_scale = no`/`include_time_in_range = no` -- each section's heading/email/notes still show, but no Time in Range chart, Sliding Scale (Reference) table, or Dose/Note columns for either. |
| [multi-meter-sliding-scale.pdf](combined/multi-meter-sliding-scale.pdf) | Same two meters, both toggles `yes`: each section gets its own Time in Range chart (independently resolved target band, defaulting to 70-180 mg/dL for both here). Alice's section additionally gets a sliding-scale table and per-reading Dose/Note columns (resolved from her own `[profile.Alice]`); Bob's section gets none of that -- he has no `sliding_scale` configured (diet-controlled, no insulin). Each section resolves independently; there's no fallback between profiles. |

## Regenerating

```bash
# Single profile (the realistic case)
trividia-truemetrix-report --config /path/to/config.ini --profile Alice --output samples/single/<name>.pdf

# Combined household view
trividia-truemetrix-report --config /path/to/config.ini --multi-meter --output samples/combined/<name>.pdf
```

See the main [README](../README.md#reports) for the full list of `[report]`
options. `--format csv` is also available (not sampled here -- these
samples are PDF only, since PDF is what you'd actually print or hand to a
doctor; CSV never shows the patient header or sliding-scale table, only
the per-reading Dose/Note columns, matching etekcity-scale-daemon's
PDF-only patient-info convention).
