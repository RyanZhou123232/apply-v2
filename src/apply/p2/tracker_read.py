"""Read-only view of tracking/tech投递2027.xlsx for duplicate checks (no writes)."""
import re, zipfile, html, urllib.parse
def rows(path):
    z = zipfile.ZipFile(path)
    ss = []
    if 'xl/sharedStrings.xml' in z.namelist():
        x = z.read('xl/sharedStrings.xml').decode()
        for si in re.findall(r'<si>(.*?)</si>', x, re.S):
            ss.append(html.unescape(''.join(re.findall(r'<t[^>]*>(.*?)</t>', si, re.S))))
    s = z.read('xl/worksheets/sheet1.xml').decode()
    rels = z.read('xl/worksheets/_rels/sheet1.xml.rels').decode() if 'xl/worksheets/_rels/sheet1.xml.rels' in z.namelist() else ''
    rid = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]*)"', rels))
    links = {ref: html.unescape(rid.get(r, '')) for ref, r in re.findall(r'<hyperlink ref="([A-Z]+\d+)" r:id="(rId\d+)"', s)}
    out = {}
    for rn, body in re.findall(r'<row r="(\d+)"[^>]*>(.*?)</row>', s, re.S):
        d = {}
        for ref, attrs, inner in re.findall(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', body, re.S):
            if not inner: continue
            if 't="s"' in attrs:
                v = ss[int(re.search(r'<v>(\d+)</v>', inner).group(1))]
            else:
                v = html.unescape(''.join(re.findall(r'<t[^>]*>(.*?)</t>', inner, re.S)) or (re.search(r'<v>(.*?)</v>', inner) or [None, ''])[1])
            d[ref] = v
        for col in 'ENPQ':
            if f'{col}{rn}' in links: d[col + '_link'] = links[f'{col}{rn}']
        if d: out[int(rn)] = d
    return out
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

_DATA_MIN_ROW = 6
_SUFFIXES = ("incorporated", "inc", "llc", "corporation", "corp", "company", "co",
             "ltd", "limited", "group", "technologies", "technology", "tech")
_STOP = set(_SUFFIXES) | {"and", "the", "of", "at"}


def company_key(s: str) -> str:
    k = norm(s)
    for suf in _SUFFIXES:
        if k.endswith(suf) and len(k) > len(suf) + 2:
            k = k[: -len(suf)]
    return k


def same_company(a: str, b: str) -> bool:
    """True when two employer names refer to the same company.

    Exact match after suffix stripping, or one name's distinctive tokens are a
    subset of the other's. Short substring matches ('netic' in 'westernmagnetics',
    'hp' vs 'hpe') are not treated as the same company.
    """
    ka, kb = company_key(a), company_key(b)
    if ka and kb and ka == kb:
        return True
    def tokens(s):
        parts = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()
        out = [t for t in parts if t and t not in _STOP and len(t) >= 3]
        if not out and parts:
            out = [t for t in parts if t and t not in _STOP]
        return out
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return set(ta) <= set(tb) or set(tb) <= set(ta)


_SEASON_YEAR = re.compile(r"(summer|winter|fall|autumn|spring)?\s*20\d{2}", re.I)


def title_core(s: str) -> str:
    """Normalized title with season/year decorations removed, for same-requisition checks."""
    s = _SEASON_YEAR.sub(" ", s or "")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def titles_same(a: str, b: str) -> bool:
    """True only when two titles are the same requisition, ignoring punctuation and 'Summer 2027'.

    'Software Development Engineer Intern, Amazon Leo' is not the same posting as
    'Software Development Engineer Intern, Machine Learning/...'.
    """
    ca, cb = title_core(a), title_core(b)
    return bool(ca) and ca == cb


def apply_url_key(url: str) -> str:
    """Stable identity for an apply URL (Greenhouse / Ashby / Workday, else host+path)."""
    u = (url or "").strip()
    if not u:
        return ""
    p = urllib.parse.urlsplit(u)
    host = (p.hostname or "").lower()
    path = p.path.rstrip("/")
    q = dict(urllib.parse.parse_qsl(p.query))
    if "greenhouse.io" in host or q.get("gh_jid") or q.get("token"):
        job = q.get("gh_jid") or q.get("token") or (re.search(r"/jobs/(\d+)", path) or [None, None])[1]
        if job:
            return "greenhouse:" + job
    if "ashbyhq.com" in host:
        bits = [b for b in path.split("/") if b and b not in ("application", "apply")]
        if len(bits) >= 2:
            return "ashby:" + "/".join(bits[:2]).lower()
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        m = re.search(r"_([A-Za-z0-9-]+)(?:/apply)?$", path)
        if m:
            return host + ":" + m[1].lower()
    path = re.sub(r"/(apply|application)$", "", path, flags=re.I)
    return (host + path).lower()


def _row_urls(d: dict) -> list[str]:
    out = []
    for k in ("Q_link", "Q", "E_link", "E"):
        v = d.get(k) or ""
        if isinstance(v, str) and v.startswith("http"):
            out.append(v)
    return out


def _data_rows(path):
    for rn, d in rows(path).items():
        if rn < _DATA_MIN_ROW:
            continue
        name = (d.get("D") or "").strip()
        if not name or name.lower() in {"company", "none"}:
            continue
        yield rn, d, name


def find_dup(path, company, role, url):
    """Same posting: same URL key, or same company + same title/requisition. Not a different role."""
    want_key = apply_url_key(url)
    ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27}|\d{7,}", url or ""))
    hits = []
    for rn, d, name in _data_rows(path):
        why = None
        for u in _row_urls(d):
            if want_key and apply_url_key(u) == want_key:
                why = "same url key"
                break
        if why is None and ids:
            blob = " ".join(str(v) for v in d.values())
            if any(i in blob for i in ids):
                why = "same job id"
        if why is None and same_company(name, company) and titles_same(d.get("F"), role):
            why = "same company+role"
        if why:
            hits.append((rn, why, d.get("D"), d.get("F")))
    return hits


def company_rows(path, company):
    return [(rn, d.get("D"), d.get("F"), d.get("L")) for rn, d, name in _data_rows(path)
            if same_company(name, company)]


def applied_dates(path, company, today=None):
    """Application dates (column G) of tracker rows for this company, as datetime.date.

    Rows with no parseable date are treated as `today` so they still count toward the cap.
    """
    from datetime import date, timedelta
    today = today or date.today()
    out = []
    for rn, d, name in _data_rows(path):
        if not same_company(name, company):
            continue
        try:
            out.append(date(1899, 12, 30) + timedelta(days=int(float(d.get("G")))))
        except (TypeError, ValueError):
            out.append(today)
    return out
