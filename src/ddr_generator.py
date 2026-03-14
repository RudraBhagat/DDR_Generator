import argparse
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz


AREA_KEYWORDS = [
    "hall",
    "bedroom",
    "master bedroom",
    "common bedroom",
    "kitchen",
    "bathroom",
    "common bathroom",
    "bedroom bathroom",
    "master bedroom bathroom",
    "parking",
    "parking area",
    "external wall",
    "ceiling",
    "wall",
    "skirting",
    "duct",
    "column",
    "beam",
    "balcony",
]

AREA_CANONICAL = {
    "bathroom": "Bathroom",
    "common bathroom": "Common Bathroom",
    "bedroom bathroom": "Bedroom Bathroom",
    "master bedroom bathroom": "Master Bedroom Bathroom",
    "master bedroom": "Master Bedroom",
    "common bedroom": "Common Bedroom",
    "bedroom": "Bedroom",
    "hall": "Hall",
    "kitchen": "Kitchen",
    "parking": "Parking",
    "parking area": "Parking Area",
    "external wall": "External Wall",
    "ceiling": "Ceiling",
    "wall": "Wall",
    "balcony": "Balcony",
}

ISSUE_KEYWORDS = [
    "damp",
    "dampness",
    "seepage",
    "leak",
    "leakage",
    "crack",
    "efflorescence",
    "hollow",
    "hollowness",
    "plumbing",
    "fungus",
    "moss",
    "rust",
    "corrosion",
]


@dataclass
class Observation:
    area: str
    text: str
    source_page: int
    source: str
    issue_type: str


@dataclass
class ImageRecord:
    source: str
    page: int
    image_index: int
    file_name: str
    rel_path: str
    width: int
    height: int
    context_text: str
    area: str


@dataclass
class ThermalFinding:
    page: int
    hotspot_c: Optional[float]
    coldspot_c: Optional[float]
    delta_c: Optional[float]
    thermal_image_name: str
    source: str


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_dedupe(text: str) -> str:
    text = normalize_space(text).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return text


def classify_issue(text: str) -> str:
    lowered = text.lower()
    for key in ISSUE_KEYWORDS:
        if key in lowered:
            return key
    return "general"


def clean_observation_text(text: str) -> str:
    text = normalize_space(text)
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"\b(yes|no)\s*$", "", text, flags=re.I).strip()
    if text.endswith("&"):
        text = text[:-1].strip()
    if text.lower().endswith(" of"):
        text = f"{text} Not Available"
    if text.lower().endswith(" joints of"):
        text = f"{text} Not Available"
    if text.lower().endswith(" on"):
        text = f"{text} Not Available"
    return text if text else "Not Available"


def canonicalize_area(area: str) -> str:
    lowered = normalize_space(area).lower()
    if lowered in AREA_CANONICAL:
        return AREA_CANONICAL[lowered]
    for key in sorted(AREA_CANONICAL.keys(), key=len, reverse=True):
        if key in lowered:
            return AREA_CANONICAL[key]
    if area == "Not Available":
        return area
    return area.title()


def extract_area_from_text(text: str) -> str:
    lowered = f" {text.lower()} "
    # Prefer longer keywords first so "master bedroom" wins before "bedroom".
    for area in sorted(AREA_KEYWORDS, key=len, reverse=True):
        if f" {area} " in lowered:
            return area.title()

    patterns = [
        r"of ([A-Za-z ]+?) of flat",
        r"at the ([A-Za-z ]+)",
        r"near ([A-Za-z ]+)",
        r"in ([A-Za-z ]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            candidate = normalize_space(match.group(1))
            if 2 <= len(candidate) <= 60:
                return candidate.title()

    return "Not Available"


def extract_pdf_lines(pdf_path: Path) -> List[Tuple[int, str]]:
    doc = fitz.open(pdf_path)
    lines: List[Tuple[int, str]] = []
    for page_idx, page in enumerate(doc, start=1):
        text = page.get_text("text")
        for line in text.splitlines():
            clean = normalize_space(line)
            if clean:
                lines.append((page_idx, clean))
    return lines


def extract_pdf_text_blocks(pdf_path: Path) -> List[Tuple[int, str]]:
    doc = fitz.open(pdf_path)
    blocks_out: List[Tuple[int, str]] = []
    for page_idx, page in enumerate(doc, start=1):
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])
        for block in blocks:
            if block.get("type") != 0:
                continue
            lines = []
            for line in block.get("lines", []):
                spans = [span.get("text", "") for span in line.get("spans", [])]
                txt = normalize_space(" ".join(spans))
                if txt:
                    lines.append(txt)
            joined = normalize_space(" ".join(lines))
            if joined:
                blocks_out.append((page_idx, joined))
    return blocks_out


def is_checklist_prompt(text: str) -> bool:
    lowered = text.lower()
    prompt_markers = [
        "are there any",
        "condition of",
        "leakage during",
        "negative side inputs",
        "positive side inputs",
        "if yes",
        "checklists",
    ]
    if "?" in text:
        return True
    return any(marker in lowered for marker in prompt_markers)


def is_noise_observation(text: str) -> bool:
    lowered = text.lower()
    noise_markers = [
        "photo ",
        "positive side description",
        "negative side description",
        "positive side photographs",
        "negative side photographs",
        "checklists",
        "flagged items",
    ]
    if lowered in {"yes", "no"}:
        return True
    return any(marker in lowered for marker in noise_markers)


def extract_inspection_observations(inspection_pdf: Path) -> List[Observation]:
    blocks = extract_pdf_text_blocks(inspection_pdf)
    observations: List[Observation] = []

    current_area = "Not Available"
    for page, line in blocks:
        line = normalize_space(line)
        line_lower = line.lower()

        if any(marker in line_lower for marker in ["impacted area", "impacted areas", "room", "location"]):
            guessed_area = extract_area_from_text(line)
            if guessed_area != "Not Available":
                current_area = canonicalize_area(guessed_area)

        is_issue_line = (
            line_lower.startswith("observed")
            or " leakage " in f" {line_lower} "
            or " damp" in line_lower
            or "seepage" in line_lower
            or "crack" in line_lower
            or "efflorescence" in line_lower
            or "hollow" in line_lower
            or "plumbing issue" in line_lower
        )
        too_short = len(line) < 20
        likely_noise = is_checklist_prompt(line)
        if is_issue_line and not too_short and not likely_noise and not is_noise_observation(line):
            line = clean_observation_text(line)
            area = canonicalize_area(extract_area_from_text(line))
            if area == "Not Available":
                area = current_area
            observations.append(
                Observation(
                    area=area,
                    text=line,
                    source_page=page,
                    source="Inspection Report",
                    issue_type=classify_issue(line),
                )
            )

    deduped: List[Observation] = []
    seen = set()
    for obs in observations:
        key = (obs.area.lower(), normalize_for_dedupe(obs.text))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(obs)

    return deduped


def nearest_text_for_image(image_bbox: Tuple[float, float, float, float], text_blocks: List[Dict]) -> str:
    ix0, iy0, ix1, iy1 = image_bbox
    icx = (ix0 + ix1) / 2.0
    icy = (iy0 + iy1) / 2.0

    best_dist = float("inf")
    best_text = ""

    for block in text_blocks:
        bx0, by0, bx1, by1 = block.get("bbox", [0, 0, 0, 0])
        bcx = (bx0 + bx1) / 2.0
        bcy = (by0 + by1) / 2.0
        dist = ((bcx - icx) ** 2 + (bcy - icy) ** 2) ** 0.5
        if dist < best_dist:
            lines = []
            for line in block.get("lines", []):
                spans = [span.get("text", "") for span in line.get("spans", [])]
                txt = normalize_space(" ".join(spans))
                if txt:
                    lines.append(txt)
            candidate = normalize_space(" ".join(lines))
            if candidate:
                best_dist = dist
                best_text = candidate

    return best_text if best_text else "Not Available"


def save_image_bytes(image_bytes: bytes, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(image_bytes)


def extract_images_with_context(pdf_path: Path, output_root: Path, source_label: str) -> List[ImageRecord]:
    doc = fitz.open(pdf_path)
    records: List[ImageRecord] = []

    for page_idx, page in enumerate(doc, start=1):
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])
        text_blocks = [b for b in blocks if b.get("type") == 0]
        image_blocks = [b for b in blocks if b.get("type") == 1]

        page_text = normalize_space(page.get_text("text"))
        page_area = canonicalize_area(extract_area_from_text(page_text))

        large_images = [
            b for b in image_blocks if int(b.get("width", 0)) >= 220 and int(b.get("height", 0)) >= 180
        ]

        img_counter = 0
        for block in large_images:
            image_bytes = block.get("image")
            if not image_bytes:
                continue

            ext = block.get("ext", "png")
            img_counter += 1
            file_name = f"{source_label.lower().replace(' ', '_')}_p{page_idx:02d}_i{img_counter:02d}.{ext}"
            rel_path = Path("images") / source_label.lower().replace(" ", "_") / file_name
            abs_path = output_root / rel_path
            save_image_bytes(image_bytes, abs_path)

            context_text = nearest_text_for_image(tuple(block.get("bbox", [0, 0, 0, 0])), text_blocks)
            if len(context_text) <= 2 or context_text.lower() in {"yes", "no"}:
                context_text = "Inspection/Thermal Evidence"
            area = canonicalize_area(extract_area_from_text(context_text))
            if area == "Not Available":
                area = page_area

            records.append(
                ImageRecord(
                    source=source_label,
                    page=page_idx,
                    image_index=img_counter,
                    file_name=file_name,
                    rel_path=str(rel_path).replace("\\", "/"),
                    width=int(block.get("width", 0)),
                    height=int(block.get("height", 0)),
                    context_text=context_text,
                    area=area,
                )
            )

    return records


def parse_first_float(pattern: str, text: str) -> Optional[float]:
    match = re.search(pattern, text, flags=re.I)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_thermal_findings(thermal_pdf: Path) -> List[ThermalFinding]:
    doc = fitz.open(thermal_pdf)
    findings: List[ThermalFinding] = []

    for page_idx, page in enumerate(doc, start=1):
        text = page.get_text("text")
        hotspot = parse_first_float(r"Hotspot\s*:\s*([0-9]+(?:\.[0-9]+)?)", text)
        coldspot = parse_first_float(r"Coldspot\s*:\s*([0-9]+(?:\.[0-9]+)?)", text)
        image_name_match = re.search(r"Thermal image\s*:\s*([A-Za-z0-9_\-.]+)", text, flags=re.I)
        image_name = image_name_match.group(1) if image_name_match else "Not Available"

        delta = None
        if hotspot is not None and coldspot is not None:
            delta = round(hotspot - coldspot, 2)

        findings.append(
            ThermalFinding(
                page=page_idx,
                hotspot_c=hotspot,
                coldspot_c=coldspot,
                delta_c=delta,
                thermal_image_name=image_name,
                source="Thermal Report",
            )
        )

    return findings


def detect_conflicts(observations: List[Observation]) -> List[str]:
    conflicts: List[str] = []
    grouped: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for obs in observations:
        grouped[(obs.area.lower(), obs.issue_type.lower())].append(obs.text.lower())

    for (area, issue_type), lines in grouped.items():
        has_positive = any("observed" in line and "no " not in line for line in lines)
        has_negative = any(
            "not observed" in line or line.startswith("no ") or " no " in line
            for line in lines
        )
        if has_positive and has_negative:
            conflicts.append(
                f"Conflicting statements for {issue_type} in {area.title()}: both presence and absence were found."
            )

    return conflicts


def estimate_severity(area: str, issue_texts: List[str], thermal_deltas: List[float]) -> Tuple[str, str]:
    merged = " ".join(issue_texts).lower()

    high_signals = ["crack", "leakage", "seepage", "external wall", "plumbing issue"]
    medium_signals = ["damp", "efflorescence", "hollow", "hollowness"]

    has_high = any(sig in merged for sig in high_signals)
    has_medium = any(sig in merged for sig in medium_signals)

    max_delta = max(thermal_deltas) if thermal_deltas else None

    if has_high:
        severity = "High"
        reason = "Leakage/crack/plumbing indicators suggest active damage that can worsen if delayed."
    elif has_medium:
        severity = "Medium"
        reason = "Moisture and finish-level issues are visible and need planned corrective action."
    else:
        severity = "Low"
        reason = "Only limited or generic observations are available for this area."

    if max_delta is not None:
        reason += f" Thermal temperature delta observed up to {max_delta:.2f}°C (area mapping not available)."

    return severity, reason


def safe_list(values: List[str]) -> List[str]:
    cleaned = [normalize_space(v) for v in values if normalize_space(v)]
    return cleaned if cleaned else ["Not Available"]


def tokenize_for_match(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop_words = {
        "the",
        "and",
        "of",
        "at",
        "in",
        "on",
        "for",
        "flat",
        "no",
        "not",
        "available",
        "observed",
        "image",
        "photo",
        "description",
        "side",
    }
    return {word for word in words if len(word) > 2 and word not in stop_words}


def is_useful_evidence_image(image: ImageRecord) -> bool:
    if image.width <= 0 or image.height <= 0:
        return False
    aspect_ratio = image.width / image.height
    if aspect_ratio > 5.0:
        return False
    lowered = image.context_text.lower()
    noise_markers = ["flagged items", "checklists", "inspection/thermal evidence"]
    return not any(marker in lowered for marker in noise_markers)


def select_best_inspection_images(
    area: str,
    issue_texts: List[str],
    pages: set[int],
    inspection_images: List[ImageRecord],
    limit: int = 4,
) -> List[ImageRecord]:
    useful_images = [img for img in inspection_images if is_useful_evidence_image(img)]
    target_tokens = tokenize_for_match(area + " " + " ".join(issue_texts))

    scored: List[Tuple[int, int, int, ImageRecord]] = []
    for img in useful_images:
        context_tokens = tokenize_for_match(img.context_text + " " + img.area)
        overlap = len(target_tokens & context_tokens)
        same_area = 1 if canonicalize_area(img.area).lower() == area.lower() else 0
        same_page = 1 if img.page in pages else 0
        scored.append((same_area, overlap, same_page, img))

    scored.sort(key=lambda item: (item[0], item[1], item[2], -item[3].page, -item[3].image_index), reverse=True)

    selected: List[ImageRecord] = []
    seen_paths = set()
    for same_area, overlap, same_page, img in scored:
        if img.rel_path in seen_paths:
            continue
        if same_area or overlap > 0 or same_page:
            selected.append(img)
            seen_paths.add(img.rel_path)
        if len(selected) >= limit:
            return selected

    nearby_pages = set()
    for page in pages:
        nearby_pages.update({page - 1, page + 1, page - 2, page + 2})

    for img in useful_images:
        if img.rel_path in seen_paths:
            continue
        if img.page in nearby_pages:
            selected.append(img)
            seen_paths.add(img.rel_path)
        if len(selected) >= limit:
            return selected

    for img in useful_images:
        if img.rel_path in seen_paths:
            continue
        selected.append(img)
        seen_paths.add(img.rel_path)
        if len(selected) >= limit:
            return selected

    return selected


def build_area_observation_sections(
    observations: List[Observation],
    inspection_images: List[ImageRecord],
    thermal_images: List[ImageRecord],
    thermal_findings: List[ThermalFinding],
) -> List[Dict]:
    area_to_obs: Dict[str, List[Observation]] = defaultdict(list)
    for obs in observations:
        area_to_obs[canonicalize_area(obs.area)].append(obs)

    area_sections: List[Dict] = []

    for area, obs_list in sorted(area_to_obs.items(), key=lambda x: x[0]):
        pages = {obs.source_page for obs in obs_list}
        issue_texts = [obs.text for obs in obs_list]

        area_inspection_images = select_best_inspection_images(
            area=area,
            issue_texts=issue_texts,
            pages=pages,
            inspection_images=inspection_images,
        )

        # Thermal report rarely includes area labels in text. Only attach if area confidently found.
        area_thermal_images = [
            img for img in thermal_images if canonicalize_area(img.area).lower() == area.lower()
        ]
        thermal_deltas = [f.delta_c for f in thermal_findings if f.delta_c is not None]
        severity, severity_reason = estimate_severity(area, issue_texts, thermal_deltas)

        root_cause = "Possible moisture ingress or material/plumbing deterioration based on observed symptoms."
        if any("crack" in t.lower() for t in issue_texts):
            root_cause = "Possible structural movement and weather exposure causing crack propagation and moisture entry."
        elif any("plumbing" in t.lower() or "leak" in t.lower() for t in issue_texts):
            root_cause = "Possible concealed plumbing leakage and failed waterproofing at wet areas."

        actions = []
        if any("plumbing" in t.lower() or "leak" in t.lower() for t in issue_texts):
            actions.append("Pressure-test and repair leaking plumbing lines and joints.")
        if any("crack" in t.lower() for t in issue_texts):
            actions.append("Inspect crack depth, stitch/seal as needed, then re-finish affected wall surfaces.")
        if any("damp" in t.lower() or "seepage" in t.lower() or "efflorescence" in t.lower() for t in issue_texts):
            actions.append("Remove damaged plaster/paint, dry substrate, and apply waterproof treatment before repainting.")
        if not actions:
            actions.append("Conduct targeted inspection and apply area-specific corrective maintenance.")

        area_sections.append(
            {
                "area": area,
                "observations": safe_list(issue_texts),
                "probable_root_cause": root_cause,
                "severity": severity,
                "severity_reasoning": severity_reason,
                "recommended_actions": safe_list(actions),
                "source_pages": sorted(pages),
                "inspection_images": [asdict(img) for img in area_inspection_images[:4]],
                "thermal_images": [asdict(img) for img in area_thermal_images[:2]],
            }
        )

    return area_sections


def top_issue_summary(area_sections: List[Dict]) -> List[str]:
    summary = []
    for section in area_sections:
        area = section["area"]
        first_obs = section["observations"][0] if section["observations"] else "Not Available"
        severity = section["severity"]
        summary.append(f"{area}: {first_obs} (Severity: {severity})")
    return summary[:8] if summary else ["Not Available"]


def build_missing_info(
    inspection_lines: List[Tuple[int, str]],
    area_sections: List[Dict],
    thermal_images: List[ImageRecord],
) -> List[str]:
    text_blob = "\n".join(line for _, line in inspection_lines)
    required_fields = [
        "Customer Name",
        "Mobile",
        "Email",
        "Address",
        "Inspection Date and Time",
    ]

    missing = []
    for field in required_fields:
        pattern = rf"{re.escape(field)}\s*:?\s*([^\n]*)"
        match = re.search(pattern, text_blob, flags=re.I)
        if not match or not normalize_space(match.group(1)):
            missing.append(f"{field}: Not Available")

    if not any(section.get("thermal_images") for section in area_sections):
        missing.append(
            "Thermal image to area mapping: Not Available (thermal pages do not consistently provide area labels in extractable text)."
        )

    if not thermal_images:
        missing.append("Thermal images: Image Not Available")

    return missing if missing else ["Not Available"]


def markdown_image_block(images: List[Dict], title: str) -> str:
    if not images:
        return f"- {title}: Image Not Available"

    lines = [f"- {title}:"]
    for img in images:
        caption = normalize_space(img.get("context_text", "")).strip() or "Image"
        rel_path = img.get("rel_path", "")
        lines.append(f"  - {caption}")
        lines.append(f"    ![{caption}]({rel_path})")
    return "\n".join(lines)


def generate_markdown_report(
    output_md: Path,
    area_sections: List[Dict],
    conflicts: List[str],
    thermal_findings: List[ThermalFinding],
    thermal_images: List[ImageRecord],
    missing_info: List[str],
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    hottest = sorted([f for f in thermal_findings if f.delta_c is not None], key=lambda x: x.delta_c, reverse=True)
    top_thermal = hottest[:6]

    md: List[str] = []
    md.append("# Main DDR (Detailed Diagnostic Report)")
    md.append("")
    md.append(f"Generated On: {now}")
    md.append("")

    md.append("## 1. Property Issue Summary")
    for line in top_issue_summary(area_sections):
        md.append(f"- {line}")
    md.append("")

    md.append("## 2. Area-wise Observations")
    if not area_sections:
        md.append("- Not Available")
    for section in area_sections:
        md.append(f"### {section['area']}")
        md.append("- Observations:")
        for obs in section["observations"]:
            md.append(f"  - {obs}")
        md.append(markdown_image_block(section.get("inspection_images", []), "Inspection Images"))
        md.append(markdown_image_block(section.get("thermal_images", []), "Thermal Images"))
        md.append("")

    md.append("## 3. Probable Root Cause")
    if not area_sections:
        md.append("- Not Available")
    for section in area_sections:
        md.append(f"- {section['area']}: {section['probable_root_cause']}")
    md.append("")

    md.append("## 4. Severity Assessment (with reasoning)")
    if not area_sections:
        md.append("- Not Available")
    for section in area_sections:
        md.append(f"- {section['area']}: {section['severity']} - {section['severity_reasoning']}")
    md.append("")

    md.append("## 5. Recommended Actions")
    if not area_sections:
        md.append("- Not Available")
    for section in area_sections:
        md.append(f"### {section['area']}")
        for action in section["recommended_actions"]:
            md.append(f"- {action}")
    md.append("")

    md.append("## 6. Additional Notes")
    if conflicts:
        md.append("- Detected conflicts:")
        for conflict in conflicts:
            md.append(f"  - {conflict}")
    else:
        md.append("- No direct text conflicts detected between extracted observations.")

    if top_thermal:
        md.append("- High thermal deltas detected (area label not reliably available in source text):")
        page_to_thermal_images: Dict[int, List[ImageRecord]] = defaultdict(list)
        for img in thermal_images:
            page_to_thermal_images[img.page].append(img)

        for finding in top_thermal:
            md.append(
                f"  - Thermal page {finding.page}: Hotspot {finding.hotspot_c}°C, Coldspot {finding.coldspot_c}°C, Delta {finding.delta_c}°C, Image {finding.thermal_image_name}"
            )
            imgs = page_to_thermal_images.get(finding.page, [])[:2]
            if imgs:
                for img in imgs:
                    caption = f"Thermal page {img.page} image {img.image_index}"
                    md.append(f"    ![{caption}]({img.rel_path})")
            else:
                md.append("    - Image Not Available")
    else:
        md.append("- Thermal numeric findings: Not Available")
    md.append("")

    md.append("## 7. Missing or Unclear Information")
    for item in missing_info:
        md.append(f"- {item}")

    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(md), encoding="utf-8")


def generate_json_output(
    output_json: Path,
    area_sections: List[Dict],
    conflicts: List[str],
    thermal_findings: List[ThermalFinding],
    missing_info: List[str],
) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(),
        "property_issue_summary": top_issue_summary(area_sections),
        "area_wise_observations": area_sections,
        "probable_root_cause": [
            {"area": sec["area"], "value": sec["probable_root_cause"]} for sec in area_sections
        ],
        "severity_assessment": [
            {
                "area": sec["area"],
                "severity": sec["severity"],
                "reasoning": sec["severity_reasoning"],
            }
            for sec in area_sections
        ],
        "recommended_actions": [
            {"area": sec["area"], "actions": sec["recommended_actions"]} for sec in area_sections
        ],
        "additional_notes": {
            "conflicts": conflicts if conflicts else ["No direct text conflicts detected"],
            "top_thermal_deltas": [asdict(f) for f in sorted(thermal_findings, key=lambda x: x.delta_c or -999, reverse=True)[:6]],
        },
        "missing_or_unclear_information": missing_info,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_pipeline(inspection_pdf: Path, thermal_pdf: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    inspection_lines = extract_pdf_lines(inspection_pdf)
    observations = extract_inspection_observations(inspection_pdf)

    inspection_images = extract_images_with_context(inspection_pdf, output_dir, "inspection")
    thermal_images = extract_images_with_context(thermal_pdf, output_dir, "thermal")

    thermal_findings = extract_thermal_findings(thermal_pdf)
    conflicts = detect_conflicts(observations)

    area_sections = build_area_observation_sections(
        observations=observations,
        inspection_images=inspection_images,
        thermal_images=thermal_images,
        thermal_findings=thermal_findings,
    )

    missing_info = build_missing_info(inspection_lines, area_sections, thermal_images)

    markdown_path = output_dir / "DDR_Report.md"
    json_path = output_dir / "DDR_Report.json"

    generate_markdown_report(
        output_md=markdown_path,
        area_sections=area_sections,
        conflicts=conflicts,
        thermal_findings=thermal_findings,
        thermal_images=thermal_images,
        missing_info=missing_info,
    )

    generate_json_output(
        output_json=json_path,
        area_sections=area_sections,
        conflicts=conflicts,
        thermal_findings=thermal_findings,
        missing_info=missing_info,
    )

    manifest = {
        "inspection_pdf": str(inspection_pdf),
        "thermal_pdf": str(thermal_pdf),
        "outputs": {
            "markdown": str(markdown_path),
            "json": str(json_path),
            "images_dir": str((output_dir / "images").resolve()),
        },
        "counts": {
            "observations": len(observations),
            "inspection_images": len(inspection_images),
            "thermal_images": len(thermal_images),
            "thermal_findings": len(thermal_findings),
            "area_sections": len(area_sections),
        },
    }

    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("DDR generation completed")
    print(json.dumps(manifest, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a DDR report from inspection and thermal PDFs.")
    parser.add_argument(
        "--inspection",
        default="input/inspection_report.pdf",
        help="Path to the inspection PDF file.",
    )
    parser.add_argument(
        "--thermal",
        default="input/thermal_report.pdf",
        help="Path to the thermal PDF file.",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="Directory to write generated DDR outputs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).parent.parent

    def resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else project_root / path

    inspection_pdf = resolve(args.inspection)
    thermal_pdf = resolve(args.thermal)
    output_dir = resolve(args.output)

    if not inspection_pdf.exists():
        raise FileNotFoundError(f"Inspection file not found: {inspection_pdf}")
    if not thermal_pdf.exists():
        raise FileNotFoundError(f"Thermal file not found: {thermal_pdf}")

    run_pipeline(inspection_pdf, thermal_pdf, output_dir)


if __name__ == "__main__":
    main()
