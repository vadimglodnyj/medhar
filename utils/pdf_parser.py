import re
from typing import List, Dict


def _normalize_year(y: str) -> str:
    # Expand 2-digit years to 20xx, assuming 19xx unlikely in current docs
    if len(y) == 2:
        yy = int(y)
        return f"20{yy:02d}"
    return y


def _find_dates(text: str) -> Dict[str, str]:
    # Normalize for Ukrainian text and dashes
    t = _normalize_uk_text(text)
    date_pattern = r"\b(\d{2})\.(\d{2})\.(\d{2,4})\b"
    result = {"date_start": None, "date_end": None}

    # Extended keyword dictionaries
    start_keywords = [
        "дата госпітал", "дата надходження", "надходження", "поступлен",
        "дата поступ", "госпіталіз", "госпіталізаці", "поступив", "поступила",
        "надійшов", "надійшла", "перебував з", "перебувала з",
    ]
    end_keywords = [
        "дата виписки", "виписки", "виписка", "виписаний", "виписана",
        "перебував по", "перебувала по", "дата смерті", "смерті",
    ]
    exclude_context = ["народження", "нарож", "р.н.", "рн", "р. н.", "dob"]

    # 1) Try ranges like 21.03.2025-09.04.2025 or 21.03-09.04.25
    range_re = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{2,4})\b\s*[\-–—]\s*(\d{2})\.(\d{2})\.(\d{2,4})?\b")
    rm = range_re.search(t)
    if rm:
        y1 = _normalize_year(rm.group(3))
        y2 = _normalize_year(rm.group(6)) if rm.group(6) else y1
        result["date_start"] = f"{rm.group(1)}.{rm.group(2)}.{y1}"
        result["date_end"] = f"{rm.group(4)}.{rm.group(5)}.{y2}"

    # Helper to find date near keywords excluding birth context
    def find_near(keywords: List[str]) -> str:
        for kw in keywords:
            m = re.search(fr"(?i){kw}[^\n\r]{{0,120}}?{date_pattern}", t)
            if m:
                snippet = t[max(0, m.start()-30):m.end()+30].lower()
                if any(ex in snippet for ex in exclude_context):
                    continue
                return f"{m.group(1)}.{m.group(2)}.{_normalize_year(m.group(3))}"
        return None

    # 2) Keyword proximity
    if not result["date_start"]:
        result["date_start"] = find_near(start_keywords)
    if not result["date_end"]:
        result["date_end"] = find_near(end_keywords)

    # 3) Fallback: pick two dates but avoid birth-context
    if not result["date_start"] or not result["date_end"]:
        all_iter = list(re.finditer(date_pattern, t))
        usable = []
        for m in all_iter:
            s = t[max(0, m.start()-30):m.end()+30].lower()
            if any(ex in s for ex in exclude_context):
                continue
            usable.append(m)
        if usable:
            if not result["date_start"]:
                d = usable[0]
                result["date_start"] = f"{d.group(1)}.{d.group(2)}.{_normalize_year(d.group(3))}"
            if len(usable) > 1 and not result["date_end"]:
                d = usable[1]
                result["date_end"] = f"{d.group(1)}.{d.group(2)}.{_normalize_year(d.group(3))}"

    return result


def _find_diagnoses(text: str) -> List[str]:
    # Normalize line endings and Ukrainian text
    txt = _normalize_uk_text(text.replace('\r\n', '\n').replace('\r', '\n'))
    lines = [ln.strip() for ln in txt.split('\n')]

    diagnoses: List[str] = []

    # Collect multi-line diagnosis after headers up to blank line or next header
    header_regex = re.compile(r"(?i)^(основн\S*\s+діагноз|повний\s+діагноз|клінічн\S*\s+діагноз|діагноз\s*при\s*виписці|виписн\S*\s+діагноз|заключн\S*\s+діагноз|діагноз)\s*[:\-–—]?")
    i = 0
    while i < len(lines):
        line = lines[i]
        if header_regex.search(line) or re.match(r"(?i)^(основн\S*\s+діагноз|повний\s+діагноз|клінічн\S*\s+діагноз|діагноз\s*при\s*виписці|виписн\S*\s+діагноз|заключн\S*\s+діагноз|діагноз)\s*[:\-–—]\s*(.+)$", line):
            # If same-line value exists, capture it
            m = re.match(r"(?i)^(?:основн\S*\s+діагноз|повний\s+діагноз|клінічн\S*\s+діагноз|діагноз\s*при\s*виписці|виписн\S*\s+діагноз|заключн\S*\s+діагноз|діагноз)\s*[:\-–—]\s*(.+)$", line)
            collected = []
            if m and m.group(1).strip():
                collected.append(m.group(1).strip())
                i += 1
            else:
                # Collect following lines until blank line or another header-like label
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if not nxt:
                        break
                    if header_regex.search(nxt):
                        break
                    collected.append(nxt)
                    # Reasonable cap to avoid runaway
                    if len(" ".join(collected)) > 600:
                        break
                    i += 1
            diag_text = _clean_diag_text(" ".join(collected))
            if diag_text and diag_text not in diagnoses:
                diagnoses.append(diag_text)
            continue
        i += 1

    # Fallback 1: search for common medical words near context
    if not diagnoses:
        m = re.search(r"(?i)(основн\S*\s+діагноз|травма|перелом|поранення|контузія|забій)[^\n\r]{0,300}", txt)
        if m:
            diagnoses.append(_clean_diag_text(m.group(0)))

    # Fallback 2: search around ICD-10 codes (e.g., S72) in glitched text
    if not diagnoses:
        # Find an ICD code and capture around it
        m = re.search(r"\b[A-Z][0-9]{2}(?:\.[0-9])?\b", txt)
        if m:
            start = max(0, m.start() - 200)
            end = min(len(txt), m.end() + 300)
            snippet = txt[start:end]
            # Trim to sentence-ish boundaries
            snippet = snippet.strip().strip(' .\n\r')
            if snippet:
                diagnoses.append(snippet)

    return diagnoses


def extract_data_from_pdf_text(text: str) -> List[Dict[str, str]]:
    dates = _find_dates(text)
    diags = _find_diagnoses(text)
    if not diags:
        diags = [""]
    episodes: List[Dict[str, str]] = []
    for d in diags:
        episodes.append({
            "date_start": dates.get("date_start"),
            "date_end": dates.get("date_end"),
            "diagnosis": d,
        })
    return episodes


def extract_data_from_pdf(pdf_path: str, force_ocr: bool = False) -> List[Dict[str, str]]:
    """Extract episodes from PDF using text first, OCR as fallback.

    Parameters:
        pdf_path: Path to PDF file
        force_ocr: If True, skip direct text extraction and use OCR only
    """
    # 1) Try direct text extraction via pypdf (fast, no OCR) unless forced OCR
    if not force_ocr:
        # a) Try PyPDF text
        try:
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            full_text_parts: List[str] = []
            for page in reader.pages:
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                if t:
                    full_text_parts.append(t)
            if full_text_parts:
                text = "\n".join(full_text_parts)
                episodes = extract_data_from_pdf_text(text)
                # If diagnosis parsed is empty or only blanks, try pdfminer
                if any(ep.get("diagnosis") for ep in episodes):
                    return episodes
        except Exception:
            pass

        # b) Try pdfminer.six high-level extract_text
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract_text
            text2 = pdfminer_extract_text(pdf_path) or ""
            if text2.strip():
                episodes2 = extract_data_from_pdf_text(text2)
                if any(ep.get("diagnosis") for ep in episodes2):
                    return episodes2
                # If dates may differ, still return if pdfminer produced anything non-empty overall
                if episodes2:
                    return episodes2
        except Exception:
            pass

    # 2) OCR fallback: pdf2image -> easyocr
    try:
        from pdf2image import convert_from_path
        import easyocr

        # Convert pages to images
        images = convert_from_path(pdf_path, dpi=200)
        reader = easyocr.Reader(["uk"], gpu=False)

        ocr_text_parts: List[str] = []
        for img in images:
            # easyocr accepts numpy arrays
            import numpy as np
            arr = np.array(img)
            res = reader.readtext(arr, detail=0, paragraph=True)
            ocr_text_parts.append("\n".join(res))

        if ocr_text_parts:
            text = "\n".join(ocr_text_parts)
            return extract_data_from_pdf_text(text)
    except Exception:
        pass

    # 3) OCR fallback 2: pdf2image -> Tesseract (ukr)
    try:
        from pdf2image import convert_from_path
        import pytesseract
        import numpy as np
        _configure_tesseract_cmd()

        images = convert_from_path(pdf_path, dpi=250)
        ocr_text_parts: List[str] = []
        for img in images:
            arr = np.array(img)
            # Try ukrainian first, then fallback to russian if ukr missing
            txt = ""
            for lang_code in ("ukr", "rus"):
                try:
                    txt = pytesseract.image_to_string(
                        arr, lang=lang_code, config="--oem 1 --psm 6"
                    )
                    if txt and txt.strip():
                        break
                except Exception:
                    continue
            if txt:
                ocr_text_parts.append(txt)
        if ocr_text_parts:
            text = "\n".join(ocr_text_parts)
            return extract_data_from_pdf_text(text)
    except Exception:
        pass

    return []



def extract_text_variants(pdf_path: str, include_ocr: bool = False) -> Dict[str, str]:
    """Return raw text by different extractors for debugging.

    Keys present when available: "pypdf", "pdfminer", and optionally "ocr".
    """
    texts: Dict[str, str] = {}

    # PyPDF
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        parts: List[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                parts.append(t)
        if parts:
            texts["pypdf"] = "\n".join(parts)
    except Exception:
        pass

    # pdfminer
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract_text
        t2 = pdfminer_extract_text(pdf_path) or ""
        if t2.strip():
            texts["pdfminer"] = t2
    except Exception:
        pass

    # OCR (optional)
    if include_ocr:
        try:
            from pdf2image import convert_from_path
            import easyocr
            import numpy as np

            images = convert_from_path(pdf_path, dpi=200)
            reader = easyocr.Reader(["uk"], gpu=False)
            ocr_parts: List[str] = []
            for img in images:
                arr = np.array(img)
                res = reader.readtext(arr, detail=0, paragraph=True)
                ocr_parts.append("\n".join(res))
            if ocr_parts:
                texts["ocr"] = "\n".join(ocr_parts)
        except Exception:
            pass

    return texts


# ------------------- Text normalization helpers -------------------

LAT_TO_CYR = {
    'A': 'А', 'B': 'В', 'C': 'С', 'E': 'Е', 'H': 'Н', 'I': 'І', 'K': 'К', 'M': 'М', 'O': 'О', 'P': 'Р', 'T': 'Т', 'X': 'Х', 'Y': 'У',
    'a': 'а', 'c': 'с', 'e': 'е', 'i': 'і', 'o': 'о', 'p': 'р', 'x': 'х', 'y': 'у', 'm': 'м', 'k': 'к', 't': 'т', 'b': 'ь',
}

def _normalize_uk_text(text: str) -> str:
    if not text:
        return text
    s = text
    # Replace zero-width and odd controls
    s = re.sub(r"[\u200B\u200C\u200D\uFEFF]", "", s)
    # Normalize dashes and quotes
    s = s.replace('–', '-').replace('—', '-').replace('−', '-')
    s = s.replace('’', "'").replace('`', "'")
    # Map Latin lookalikes to Cyrillic to improve header matching
    s = ''.join(LAT_TO_CYR.get(ch, ch) for ch in s)
    # Collapse repeated punctuation and spaces
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+\n", "\n", s)
    return s

def _clean_diag_text(text: str) -> str:
    if not text:
        return ""
    s = _normalize_uk_text(text)
    # Remove leading header remnants and excessive symbols
    s = re.sub(r"(?i)^(основн\S*\s+діагноз|повний\s+діагноз|клінічн\S*\s+діагноз|виписн\S*\s+діагноз|заключн\S*\s+діагноз|діагноз)\s*[:\-–—]\s*", "", s)
    s = re.sub(r"[^\w\s\.\,\-\(\)/]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    # Prefer to cut long tail after 400 chars
    if len(s) > 400:
        s = s[:400].rsplit(' ', 1)[0]
    return s


def _configure_tesseract_cmd() -> None:
    """Try to auto-detect tesseract.exe on Windows if not in PATH.

    Checks common install paths and sets pytesseract.pytesseract.tesseract_cmd.
    Safe to call even if tesseract already configured.
    """
    try:
        import pytesseract
        import os
        # If already set, keep
        cmd = getattr(pytesseract.pytesseract, 'tesseract_cmd', None)
        if cmd and os.path.isfile(cmd):
            return
        candidates = [
            r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
            r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                pytesseract.pytesseract.tesseract_cmd = c
                return
    except Exception:
        pass

