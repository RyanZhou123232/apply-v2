"""append_applications writes the live tracker in place, with no backup copy, then read-back."""
import zipfile
from datetime import date
from pathlib import Path

from apply import tracker_write
from apply.p2 import tracker_read


def _tracker(path: Path) -> None:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    sheet = (
        f'<worksheet xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<dimension ref=\"D5:Q6\"/><sheetData>"
        "<row r=\"5\" spans=\"4:17\">"
        "<c r=\"D5\" t=\"inlineStr\"><is><t>Company</t></is></c>"
        "<c r=\"F5\" t=\"inlineStr\"><is><t>Title</t></is></c>"
        "<c r=\"L5\" t=\"inlineStr\"><is><t>status</t></is></c>"
        "</row>"
        "<row r=\"6\" spans=\"4:17\">"
        "<c r=\"D6\" s=\"1\" t=\"inlineStr\"><is><t>Nex</t></is></c>"
        "<c r=\"E6\" s=\"1\" t=\"inlineStr\"><is><t>https://example.com/nex</t></is></c>"
        "<c r=\"F6\" s=\"1\" t=\"inlineStr\"><is><t>SWE Intern</t></is></c>"
        "<c r=\"G6\" s=\"24\"><v>46335</v></c>"
        "<c r=\"L6\" s=\"1\" t=\"inlineStr\"><is><t>Applied</t></is></c>"
        "<c r=\"Q6\" s=\"15\" t=\"inlineStr\"><is><t>https://example.com/nex</t></is></c>"
        "</row></sheetData>"
        "<hyperlinks><hyperlink ref=\"Q6\" r:id=\"rId1\"/></hyperlinks>"
        "</worksheet>"
    )
    rels = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{rel_ns}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        'Target="https://example.com/nex" TargetMode="External"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", rels)
        z.writestr("[Content_Types].xml", "<Types/>")


def test_append_application_updates_live_file_with_no_backup(tmp_path):
    tracking = tmp_path / "tracking"
    tracking.mkdir()
    xlsx = tracking / "tech投递2027.xlsx"
    _tracker(xlsx)
    before = set(tmp_path.rglob("*"))
    row = tracker_write.append_application(
        xlsx,
        company="UiPath",
        url="https://jobs.ashbyhq.com/uipath/4983eab7-099d-4c77-9a5a-8dde72084c04/application",
        role="Software Engineer Intern",
        note="Ashby confirmed success.",
        resume_rel="file:///E:/求职/简历/Tech/SWE/Ryan Zhou Resume 28.pdf",
        shot_rel="../runs/ashby-4983eab7-099d-4c77-9a5a-8dde72084c04/submission-confirmation.png",
        status="Applied",
        date=date(2026, 11, 10),
    )
    assert row == 7
    after = set(tmp_path.rglob("*"))
    created = {p.relative_to(tmp_path).as_posix() for p in after - before}
    assert created == set(), created
    assert not (tracking / "backups").exists()
    assert not list(tmp_path.rglob("*.backup-*"))
    assert not list(tmp_path.rglob("*.bak*"))
    assert not list(tmp_path.rglob("*.tmp.xlsx"))
    d = tracker_read.rows(xlsx)[7]
    assert d["D"] == "UiPath"
    assert d["F"] == "Software Engineer Intern"
    assert d["L"] == "Applied"
    assert d["G"] == "46336"
    assert d["Q"] == "https://jobs.ashbyhq.com/uipath/4983eab7-099d-4c77-9a5a-8dde72084c04/application"
    assert d["P"] == "View confirmation"
    assert d["N"] == "Submitted PDF"
    assert d["P_link"].endswith("submission-confirmation.png")
    assert "Ryan" in d["N_link"] and d["N_link"].startswith("file:")
    # original row untouched
    assert tracker_read.rows(xlsx)[6]["D"] == "Nex"


def test_append_applications_second_batch_stays_in_place(tmp_path):
    xlsx = tmp_path / "tech投递2027.xlsx"
    _tracker(xlsx)
    rows = tracker_write.append_applications(
        xlsx,
        [
            {"company": "One", "url": "https://example.com/1", "role": "Intern A", "status": "Applied"},
            {"company": "Two", "url": "https://example.com/2", "role": "Intern B", "status": "Applied"},
        ],
        applied_date=date(2026, 11, 10),
    )
    assert rows == [7, 8]
    assert not list(tmp_path.glob("*.backup-*"))
    got = tracker_read.rows(xlsx)
    assert got[7]["D"] == "One" and got[8]["D"] == "Two"
    assert got[6]["D"] == "Nex"


def test_excel_serial_for_nov_10_2026():
    assert tracker_write.excel_serial(date(2026, 11, 10)) == 46336
