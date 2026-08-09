# Sample reports

These PDFs show what `trividia-truemetrix-report` produces for the main
`[report]` config combinations, so you can see what a layout looks like
before setting it up. All are scoped to a single profile (Alice, via
`--profile Alice`) -- **that's the realistic case**: a report you'd
actually hand to a doctor covers one patient, not a household. The
multi-meter/combined samples at the bottom are a secondary household
convenience, not the primary use case.

Rendered from a 14-reading fixture dataset spanning several days,
including one high and one low reading.

## Layout x unit x date format (full layout)

| File | Layout | Unit | Date format |
|---|---|---|---|
| [full-mgdl-world.pdf](full-mgdl-world.pdf) | full | mg/dL | world |
| [full-mgdl-us.pdf](full-mgdl-us.pdf) | full | mg/dL | us |
| [full-mmol-world.pdf](full-mmol-world.pdf) | full | mmol/L | world |
| [full-mmol-us.pdf](full-mmol-us.pdf) | full | mmol/L | us |

The `full` layout is the only one where `include_device_id`/`include_model`/
`include_profile` matter; all four samples above show every optional column
(the default).

## Simple layout

| File | Unit |
|---|---|
| [simple-mgdl-world.pdf](simple-mgdl-world.pdf) | mg/dL |
| [simple-mmol-world.pdf](simple-mmol-world.pdf) | mmol/L |

Date/glucose only, in side-by-side column pairs -- column toggles have no
effect on this layout.

## Chart layout

| File |
|---|
| [chart-mgdl-world.pdf](chart-mgdl-world.pdf) |

A line chart of glucose over time instead of a table.
`include_device_id`/`include_model`/`include_profile` have no effect here.

## Sliding scale (the report that actually matters for a doctor visit)

| File |
|---|
| [single-profile-alice-sliding-scale.pdf](single-profile-alice-sliding-scale.pdf) |

`--profile Alice` with `report.include_sliding_scale = yes`: adds Dose/Note
columns, looked up per reading from Alice's `[profile.Alice] sliding_scale`
-- see the main README's [Reports](../README.md#reports) section for the
config format and its disclaimer. **This is a display lookup of a table
Alice's own doctor already prescribed -- it is not computed, validated, or
recommended by this tool.** The fixture includes one reading that falls in
the "call doctor" band (301+) and one below range ("do not dose"), so both
show up here. The `[profile.Alice]` section behind this sample:

```ini
[profile.Alice]
name = Alice Smith
device_ids = Trividia-BLU-12345678
sliding_scale =
    :70:0:Below range -- treat hypoglycemia first, do not dose
    71:150:0
    151:200:2
    201:250:4
    251:300:6
    301::8:Above range -- call doctor
```

## Regenerating

```bash
trividia-truemetrix-report --config /path/to/config.ini --profile <name> --output samples/<name>.pdf
```

See the main [README](../README.md#reports) for the full list of `[report]`
options. `--format csv` is also available (not sampled here -- these
samples are PDF only, since PDF is what you'd actually print or hand to a
doctor).

---

## Household / combined (secondary, optional)

Everything above is single-profile. These two show the alternative
`--multi-meter` mode -- one PDF, one section per meter, useful for a
household tracking more than one person's readings together, but not the
report you'd take to a doctor visit.

| File | Shows |
|---|---|
| [multi-meter-mgdl-world.pdf](multi-meter-mgdl-world.pdf) | Both Alice's and Bob's meters, one section each, plain full layout. |
| [combined-multi-meter-sliding-scale.pdf](combined-multi-meter-sliding-scale.pdf) | Same, with `include_sliding_scale = yes`: Alice's section shows Dose/Note columns (resolved from her own `sliding_scale`), Bob's section has none -- he has no `sliding_scale` configured (diet-controlled, no insulin). Each section resolves independently; there's no fallback between profiles. |

Bob's profile behind these two samples has no `sliding_scale` line at all
(just `[profile.Bob]` with `name`/`device_ids`), which is why his section
never shows Dose/Note columns even with `include_sliding_scale = yes`.
