"""Employer / ATS account records (tracking/公司账号.xlsx) and sign-up passwords.

Ryan's rule (2026-11-09): every employer/ATS sign-up uses the Andrew email and his shared password from the
environment (WORKDAY_PASSWORD). If a site rejects it for complexity, use the site's suggested strong password,
else generate one that meets the site's stated rules. The password actually used is written to 公司账号.xlsx
right after the account is created, by editing the live workbook in place on Ryan's PC
(apply.excel_paths.LIVE_ACCOUNTS). Do not copy the file to the box, edit it, and copy it back;
do not write a .bak, .backup-*.xlsx, or tracking\backups copy. After the write, the new row
is read back and checked. Passwords are never logged or printed: this module only ever reports
lengths / booleans about them.
"""
from __future__ import annotations

import os
import re
import secrets
import string
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

APPLICATION_EMAIL = "ryanzhou@andrew.cmu.edu"
PASSWORD_ENV = "WORKDAY_PASSWORD"
SPECIALS = "!@#$%^&*"


@dataclass
class Credentials:
    email: str
    password: str = field(repr=False)
    source: str = "shared"   # "shared" (env) | "site_suggested" | "generated"

    def __repr__(self) -> str:  # never show the password
        return f"Credentials(email={self.email!r}, source={self.source!r}, password=<hidden len={len(self.password)}>)"


def shared_credentials(email: str = APPLICATION_EMAIL) -> Optional[Credentials]:
    pw = os.environ.get(PASSWORD_ENV) or ""
    return Credentials(email, pw, "shared") if pw else None


def parse_password_rules(text: str) -> dict:
    """Read a site's stated password rules ('at least 8 characters', 'one uppercase', 'special character' ...)."""
    t = text or ""
    m = re.search(r"(?:at least|minimum(?: of)?|min\.?)\s*(\d{1,2})\s*(?:characters|chars)", t, re.I) \
        or re.search(r"(\d{1,2})\s*(?:or more|\+)\s*characters", t, re.I)
    mx = re.search(r"(?:at most|maximum(?: of)?|no more than|max\.?)\s*(\d{1,3})\s*(?:characters|chars)", t, re.I)
    allowed = re.search(r"special characters?[^.:]*?[:(]\s*([!-/:-@\[-`{-~]{3,})", t)
    return {
        "min_len": int(m.group(1)) if m else 8,
        "max_len": int(mx.group(1)) if mx else 64,
        "upper": bool(re.search(r"upper ?case|capital", t, re.I)),
        "lower": bool(re.search(r"lower ?case", t, re.I)),
        "digit": bool(re.search(r"\bnumber|numeric|digit", t, re.I)),
        "special": bool(re.search(r"special|symbol|non-?alphanumeric", t, re.I)),
        "specials": "".join(c for c in (allowed.group(1) if allowed else SPECIALS) if c in string.punctuation) or SPECIALS,
    }


def is_password_rejection(text: str) -> bool:
    return bool(re.search(r"password (must|should|does not|doesn'?t|is too|needs|requirements?)|"
                          r"(must|should) (contain|include|have) (at least|a|one)|password.{0,40}(weak|complex|strength|invalid)",
                          text or "", re.I))


def generate_password(rules_text: str = "") -> str:
    """Strong password meeting the stated rules (always upper+lower+digit+special, length >= max(16, min+4))."""
    r = parse_password_rules(rules_text)
    n = max(16, r["min_len"] + 4)
    n = min(n, r["max_len"])
    specials = r["specials"]
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, specials]
    chars = [secrets.choice(p) for p in pools]
    allc = string.ascii_letters + string.digits + specials
    chars += [secrets.choice(allc) for _ in range(n - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def satisfies(password: str, rules_text: str) -> bool:
    r = parse_password_rules(rules_text)
    if not (r["min_len"] <= len(password) <= r["max_len"]):
        return False
    checks = [(r["upper"], any(c.isupper() for c in password)), (r["lower"], any(c.islower() for c in password)),
              (r["digit"], any(c.isdigit() for c in password)), (r["special"], any(not c.isalnum() for c in password))]
    return all(have for need, have in checks if need)


# ------------------------------------------------------------------ 公司账号.xlsx (columns D Company, E Email, F PW)
def _cell(ref: str, text: str, style: str = "2") -> str:
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def append_account(xlsx: Path, company: str, email: str, password: str) -> int:
    """Append one row (company, email, password) after the last used row, editing only sheet1.xml.

    Writes in place to the live workbook; no backup copy is created. After the write, the new
    row is read back and checked (company, email, and password match) before returning the
    row number. The password is never logged or included in error messages.
    """
    xlsx = Path(xlsx)
    with zipfile.ZipFile(xlsx) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    name = "xl/worksheets/sheet1.xml"
    sheet = next(d for i, d in items if i.filename == name).decode("utf-8")
    rows = [int(n) for n in re.findall(r'<row [^>]*\br="(\d+)"', sheet)]
    used = [int(n) for n in re.findall(r'<c r="[A-Z]+(\d+)"[^>]*>(?:<v>|<is>)', sheet)]
    new_r = max(used or rows or [5]) + 1
    row_xml = (f'<row r="{new_r}" spans="4:6">' + _cell(f"D{new_r}", company) + _cell(f"E{new_r}", email)
               + _cell(f"F{new_r}", password) + "</row>")
    m = re.search(rf'<row [^>]*\br="{new_r}"[^>]*?(/>|>.*?</row>)', sheet, re.S)
    if m:  # an empty pre-formatted row exists: replace it
        sheet = sheet[:m.start()] + row_xml + sheet[m.end():]
    else:
        later = [mm for mm in re.finditer(r'<row [^>]*\br="(\d+)"', sheet) if int(mm.group(1)) > new_r]
        pos = later[0].start() if later else sheet.index("</sheetData>")
        sheet = sheet[:pos] + row_xml + sheet[pos:]
    dim = re.search(r'<dimension ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"/>', sheet)
    if dim and int(dim.group(4)) < new_r:
        sheet = sheet.replace(dim.group(0), f'<dimension ref="{dim.group(1)}{dim.group(2)}:{dim.group(3)}{new_r}"/>')
    tmp = xlsx.with_name(f"{xlsx.stem}.tmp.xlsx")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for info, data in items:
                out.writestr(info, sheet.encode("utf-8") if info.filename == name else data)
        os.replace(tmp, xlsx)
    finally:
        if tmp.exists():
            tmp.unlink()
    got_company, got_email, password_ok = _read_back_account_row(xlsx, new_r, password)
    if got_company != company or got_email != email or not password_ok:
        raise RuntimeError(
            f"read-back mismatch at row {new_r}: "
            f"company_ok={got_company == company} email_ok={got_email == email} password_ok={password_ok}"
        )
    return new_r


def _read_back_account_row(xlsx: Path, row: int, password: str) -> tuple[str, str, bool]:
    """Return (company, email, password_matches). The password value is not returned."""
    with zipfile.ZipFile(xlsx) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        ss = z.read("xl/sharedStrings.xml").decode("utf-8") if "xl/sharedStrings.xml" in z.namelist() else ""
    strs = [re.sub(r"<[^>]+>", "", s) for s in re.findall(r"<si>(.*?)</si>", ss, re.S)]

    def val(cell: str) -> str:
        if 't="s"' in cell:
            v = re.search(r"<v>(\d+)</v>", cell)
            return strs[int(v.group(1))] if v else ""
        t = re.search(r"<t[^>]*>(.*?)</t>", cell, re.S) or re.search(r"<v>(.*?)</v>", cell)
        if not t:
            return ""
        return t.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")

    m = re.search(rf'<row [^>]*\br="{row}"[^>]*>(.*?)</row>', sheet, re.S)
    if not m:
        return "", "", False
    cells = dict(re.findall(r'<c r="([DEF])\d+"([^>]*>.*?</c>|[^>]*/>)', m.group(1), re.S))
    # findall group 2 is attrs+inner; prepend dummy so val() sees the full cell
    def cell_xml(col: str) -> str:
        g = cells.get(col, "")
        return f"<c r='{col}'{g}" if g else ""
    return val(cell_xml("D")), val(cell_xml("E")), val(cell_xml("F")) == password


def account_rows(xlsx: Path) -> list[tuple[str, str]]:
    """(company, email) pairs already recorded; passwords are not returned."""
    with zipfile.ZipFile(xlsx) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        ss = z.read("xl/sharedStrings.xml").decode("utf-8") if "xl/sharedStrings.xml" in z.namelist() else ""
    strs = [re.sub(r"<[^>]+>", "", s) for s in re.findall(r"<si>(.*?)</si>", ss, re.S)]

    def val(cell: str) -> str:
        if 't="s"' in cell:
            v = re.search(r"<v>(\d+)</v>", cell)
            return strs[int(v.group(1))] if v else ""
        t = re.search(r"<t[^>]*>(.*?)</t>", cell, re.S) or re.search(r"<v>(.*?)</v>", cell)
        return t.group(1) if t else ""
    out = []
    for row in re.findall(r"<row [^>]*>(.*?)</row>", sheet, re.S):
        cells = dict(re.findall(r'<c r="([DE])\d+"([^>]*>.*?</c>|[^>]*/>)', row, re.S))
        out.append((val(cells.get("D", "")), val(cells.get("E", ""))))
    return out
