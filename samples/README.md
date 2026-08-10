# Sample reports

These PDFs show what `trividia-truemetrix-report` produces for the main
`[report]` config combinations, so you can see what a layout looks like
before setting it up. Rendered from a 14-reading fixture dataset spanning
several days, including one high and one low reading.

**[`single/`](single/) is the realistic case**: a report for one patient
(Alice), the kind you'd actually hand to a doctor. **[`combined/`](combined/)
is a secondary household convenience** -- both meters in one PDF -- not
the primary use case.

Every `single/` sample has `report.include_sliding_scale = yes`, so all of
them show this header, in order:

- **Patient** / **Email** / **Meter** / **Notes** -- Meter (one
  `Meter: <device_id> (<model>)` line per distinct meter in the report)
  sits right under Email. Patient/Meter are unconditional whenever
  `--profile` is given (Meter shows even without a profile); Email/Notes
  only appear if set. See
  [config-example.ini](../config/trividia-truemetrix-daemon.ini.example)'s
  `[profile.Alice]` for the `name`/`email`/`notes` values behind these.
- A **Sliding Scale (Reference) table**: every configured band listed
  once, separate from the per-reading Dose/Note columns below.

Device ID and Model deliberately live only in that header now, not as
per-row table columns -- with Dose/Note columns already in the mix, a
per-row Device ID (a long string) was pushing the table off the page edge.
`report.include_device_id`/`include_model` still work, but only for
`--format csv`, which has no page-width constraint forcing a choice.

## single/ -- layout x unit x date format (full layout)

| File | Layout | Unit | Date format |
|---|---|---|---|
| [full-mgdl-world.pdf](single/full-mgdl-world.pdf) | full | mg/dL | world |
| [full-mgdl-us.pdf](single/full-mgdl-us.pdf) | full | mg/dL | us |
| [full-mmol-world.pdf](single/full-mmol-world.pdf) | full | mmol/L | world |
| [full-mmol-us.pdf](single/full-mmol-us.pdf) | full | mmol/L | us |

The `full` layout is the only one with per-reading Dose/Note columns (and
`include_profile`); all four samples above show every optional column
(the default). The fixture includes one reading that falls in the "call
doctor" band (301+) and one below range ("do not dose"), so both show up
in the per-reading columns here.

## single/ -- simple layout

| File | Unit |
|---|---|
| [simple-mgdl-world.pdf](single/simple-mgdl-world.pdf) | mg/dL |
| [simple-mmol-world.pdf](single/simple-mmol-world.pdf) | mmol/L |

Date/glucose only, in side-by-side column pairs. The patient header and
sliding-scale reference table still appear (they're layout-independent);
only the per-reading Dose/Note columns are full-layout-only.

## single/ -- chart layout

| File |
|---|
| [chart-mgdl-world.pdf](single/chart-mgdl-world.pdf) |

A line chart of glucose over time instead of a table. Same as `simple`:
the header and reference table still appear above the chart.

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
| [multi-meter-mgdl-world.pdf](combined/multi-meter-mgdl-world.pdf) | Both Alice's and Bob's meters, `include_sliding_scale = no` -- each section's heading/email/notes still show, but no Sliding Scale (Reference) table and no Dose/Note columns for either. |
| [multi-meter-sliding-scale.pdf](combined/multi-meter-sliding-scale.pdf) | Same two meters, `include_sliding_scale = yes`: Alice's section additionally gets a sliding-scale table and per-reading Dose/Note columns (resolved from her own `[profile.Alice]`); Bob's section gets none of that -- he has no `sliding_scale` configured (diet-controlled, no insulin). Each section resolves independently; there's no fallback between profiles. |

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
