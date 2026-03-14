# DDR Generator - Applied AI Builder Assignment

This project builds an AI-assisted workflow that converts:
- Inspection Report PDF
- Thermal Report PDF

into a structured Main DDR (Detailed Diagnostic Report) with extracted evidence images.

## What This Solution Produces

After running the pipeline, it creates:
- `output/DDR_Report.md` (client-friendly structured DDR)
- `output/DDR_Report.json` (machine-readable structured DDR)
- `output/images/inspection/*` (inspection evidence images extracted from PDF)
- `output/images/thermal/*` (thermal evidence images extracted from PDF)
- `output/run_manifest.json` (execution metadata and counts)

## How It Meets The Assignment Requirements

1. Property Issue Summary
- Auto-generated from extracted observations grouped by area.

2. Area-wise Observations
- Observations are extracted from inspection text blocks and grouped by detected area.
- Relevant inspection images are attached under each area where possible.
- If no reliable image mapping is found, it prints `Image Not Available`.

3. Probable Root Cause
- Rule-based probable causes are generated from issue patterns (leakage/crack/dampness/plumbing).

4. Severity Assessment (with reasoning)
- Severity is estimated using detected issue signals and thermal delta context.
- Reasoning text is included for each area.

5. Recommended Actions
- Action suggestions are generated per area based on issue type.

6. Additional Notes
- Includes conflict detection summary.
- Includes top thermal findings and extracted thermal images.

7. Missing or Unclear Information
- Explicitly reports `Not Available` when mapping/details are missing.
- Explicitly reports thermal area-mapping uncertainty when labels are not available in extractable text.

## Input Files

Expected in `input/`:
- `inspection_report.pdf`
- `thermal_report.pdf`

(Your workspace also includes uploaded duplicates. This pipeline uses the above pair by default.)

## Run Instructions

1. Create/activate environment and install dependencies (already done in this workspace):

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. Run generator:

```powershell
.venv\Scripts\python.exe src\ddr_generator.py --inspection input\inspection_report.pdf --thermal input\thermal_report.pdf --output output
```

3. Open result files:
- `output/DDR_Report.md`
- `output/DDR_Report.json`

## System Design Summary

- PDF text extraction: PyMuPDF block-level extraction.
- Observation extraction: keyword + pattern based filtering, deduplication, area inference.
- Image extraction: rendered image blocks from PDF pages (with size threshold to avoid tiny assets).
- Image-to-area linking:
  - Primary: nearest text context around image.
  - Fallback: page-level area inference.
- Conflict handling: detects positive-vs-negative statements for same area/issue.
- Missing data policy: explicit `Not Available` / `Image Not Available`.

## Reliability Decisions

- No facts are invented beyond source content.
- If thermal area mapping is not explicit in extractable text, output states this clearly.
- Duplicate statements are reduced using normalization-based dedupe.

## Known Limitations

- Some inspection PDF lines are fragmented due source layout, so a few observations may be partially truncated.
- Thermal report in this sample does not consistently expose area labels in extractable text, so direct per-area thermal mapping may remain unavailable.
- Rule-based severity is conservative and not calibrated by domain expert labels.

## Improvement Roadmap

- Add OCR and layout-aware model for better line reconstruction in scanned/complex pages.
- Add semantic matching between thermal and inspection evidence (visual-language linking).
- Add confidence scores for each extracted field.
- Add optional LLM review pass constrained by source citations.
- Export final report to DOCX/PDF template for direct client delivery.
