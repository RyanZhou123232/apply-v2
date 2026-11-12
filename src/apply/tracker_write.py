"""Append rows to tracking/tech投递2027.xlsx in place, with no backup copy.

Run this on Ryan's PC against the live workbook (see apply.excel_paths.LIVE_TRACKER).
Do not copy the file to the box, edit it, and copy it back. After the write, the
new row is read back and checked before returning. No .bak, no .backup-*.xlsx,
and no tracking/backups folder is created.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from apply.excel_paths import LIVE_TRACKER
from apply.p2 import tracker_read

# Excel's 1900-based serial origin (with the Lotus leap-year bug).
_EXCEL_EPOCH = date(1899, 12, 30)


def excel_serial(d: date | datetime | int | float | None = None) -> int:
    if isinstance(d, (int, float)):
        return int(d)
    if isinstance(d, datetime):
        d = d.date()
    if d is None:
        d = date.today()
    return (d - _EXCEL_EPOCH).days


def _cell(ref: str, text: str, style: str) -> str:
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def _hyperlink_target(value: str) -> str:
    v = (value or "").strip()
    if re.match(r"^(https?:|file:|mailto:)", v, re.I):
        return v
    if re.match(r"^[A-Za-z]:[\\/]", v):
        return "file:///" + quote(v.replace("\\", "/"), safe="/:")
    return quote(v, safe="/.-_()")


def _rewrite_zip(xlsx: Path, updates: dict[str, bytes]) -> None:
    """Replace selected zip members in the live workbook. No backup; temp file is removed."""
    xlsx = Path(xlsx)
    tmp = xlsx.with_name(f"{xlsx.stem}.tmp.xlsx")
    try:
        with ZipFile(xlsx) as zin:
            items = [(info, zin.read(info.filename)) for info in zin.infolist()]
        with ZipFile(tmp, "w", ZIP_DEFLATED) as zout:
            for info, data in items:
                zout.writestr(info, updates.get(info.filename, data))
        os.replace(tmp, xlsx)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_applications(
    xlsx: Path,
    entries: list[dict],
    applied_date: date | datetime | int | float | None = None,
) -> list[int]:
    """Append one or more tracker rows to the live workbook and read them back.

    Each entry may have: company, url, role, note, resume_rel, shot_rel,
    status (default "Applied"), and optional date (overrides applied_date).
    Returns the new row numbers.
    """
    xlsx = Path(xlsx)
    if not entries:
        return []
    default_serial = excel_serial(applied_date)

    with ZipFile(xlsx) as zin:
        sheet = zin.read("xl/worksheets/sheet1.xml").decode("utf-8")
        rels_name = "xl/worksheets/_rels/sheet1.xml.rels"
        rels = zin.read(rels_name).decode("utf-8") if rels_name in zin.namelist() else (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
        )
        has_rels = rels_name in zin.namelist()

    used = [int(r) for r in re.findall(r'<row r="(\d+)"', sheet)]
    filled = {
        int(r)
        for r, body in re.findall(r'<row r="(\d+)"[^>]*>(.*?)</row>', sheet, re.S)
        if re.search(r"<[vt][ >]", body)
    }
    rid_nums = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
    next_rid = (max(rid_nums) + 1) if rid_nums else 1
    start = max(filled or used or [5]) + 1

    newrels, newlinks, written = [], [], []
    expected: list[dict] = []
    for i, e in enumerate(entries):
        rn = start + i
        serial = excel_serial(e["date"]) if e.get("date") is not None else default_serial
        company = e["company"]
        url = e["url"]
        role = e["role"]
        note = e.get("note") or ""
        status = e.get("status") or "Applied"
        resume_rel = e.get("resume_rel") or ""
        shot_rel = e.get("shot_rel") or ""
        ids: dict[str, str] = {}
        for col, target in (("N", resume_rel), ("P", shot_rel), ("Q", url)):
            if not target:
                continue
            rid = f"rId{next_rid}"
            next_rid += 1
            ids[col] = rid
            href = _hyperlink_target(target)
            newrels.append(
                f'<Relationship Id="{rid}" '
                f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                f'Target="{escape(href, {chr(34): "&quot;"})}" TargetMode="External"/>'
            )
            newlinks.append(f'<hyperlink ref="{col}{rn}" r:id="{rid}"/>')
        row = (
            f'<row r="{rn}" spans="4:17" x14ac:dyDescent="0.4">'
            + _cell(f"D{rn}", company, "1")
            + _cell(f"E{rn}", url, "1")
            + _cell(f"F{rn}", role, "1")
            + f'<c r="G{rn}" s="24"><v>{serial}</v></c>'
            + "".join(f'<c r="{c}{rn}" s="1"/>' for c in "HIJK")
            + _cell(f"L{rn}", status, "1")
            + _cell(f"M{rn}", note, "1")
            + (_cell(f"N{rn}", "Submitted PDF", "23") if resume_rel else '<c r="N{0}" s="1"/>'.format(rn))
            + f'<c r="O{rn}" s="1"/>'
            + (_cell(f"P{rn}", "View confirmation", "15") if shot_rel else '<c r="P{0}" s="1"/>'.format(rn))
            + _cell(f"Q{rn}", url, "15")
            + "</row>"
        )
        if rn in used:
            sheet = re.sub(
                rf'<row r="{rn}"[^>]*>.*?</row>|<row r="{rn}"[^>]*/>',
                lambda m, row=row: row,
                sheet,
                count=1,
                flags=re.S,
            )
        else:
            sheet = sheet.replace("</sheetData>", row + "</sheetData>", 1)
        written.append(rn)
        expected.append({
            "row": rn,
            "company": company,
            "role": role,
            "status": status,
            "url": url,
            "serial": str(serial),
            "resume_rel": resume_rel,
            "shot_rel": shot_rel,
        })

    last = max((used or [0]) + written)
    sheet = re.sub(
        r'<dimension ref="([A-Z]+\d+):([A-Z]+)\d+"/>',
        lambda m: f'<dimension ref="{m.group(1)}:{m.group(2)}{last}"/>',
        sheet,
        count=1,
    )
    if newlinks:
        if "</hyperlinks>" in sheet:
            sheet = sheet.replace("</hyperlinks>", "".join(newlinks) + "</hyperlinks>", 1)
        else:
            sheet = sheet.replace("</worksheet>", "<hyperlinks>" + "".join(newlinks) + "</hyperlinks></worksheet>", 1)
    if newrels:
        if "</Relationships>" not in rels:
            rels = rels.rstrip() + "</Relationships>"
        rels = rels.replace("</Relationships>", "".join(newrels) + "</Relationships>", 1)

    updates = {"xl/worksheets/sheet1.xml": sheet.encode("utf-8")}
    if newrels or has_rels:
        updates[rels_name] = rels.encode("utf-8")
    _rewrite_zip(xlsx, updates)

    got = tracker_read.rows(xlsx)
    for exp in expected:
        rn = exp["row"]
        d = got.get(rn) or {}
        resume_ok = (not exp["resume_rel"]) or (d.get("N_link") or "") == _hyperlink_target(exp["resume_rel"]) or (d.get("N_link") or "") == exp["resume_rel"]
        shot_ok = (not exp["shot_rel"]) or (d.get("P_link") or "") == _hyperlink_target(exp["shot_rel"]) or (d.get("P_link") or "") == exp["shot_rel"]
        url_ok = (d.get("Q") == exp["url"] or d.get("E") == exp["url"] or d.get("Q_link") == exp["url"])
        if not (
            d.get("D") == exp["company"]
            and d.get("F") == exp["role"]
            and d.get("L") == exp["status"]
            and str(d.get("G") or "") == exp["serial"]
            and url_ok
            and resume_ok
            and shot_ok
        ):
            raise RuntimeError(
                f"read-back mismatch at tracker row {rn}: "
                f"company_ok={d.get('D') == exp['company']} "
                f"role_ok={d.get('F') == exp['role']} "
                f"status_ok={d.get('L') == exp['status']} "
                f"date_ok={str(d.get('G') or '') == exp['serial']} "
                f"url_ok={url_ok} resume_link_ok={resume_ok} confirmation_link_ok={shot_ok}"
            )
    return written


def append_application(xlsx: Path, **entry) -> int:
    """Append a single tracker row. See append_applications for entry fields."""
    return append_applications(xlsx, [entry])[0]


__all__ = [
    "LIVE_TRACKER",
    "append_application",
    "append_applications",
    "excel_serial",
]
