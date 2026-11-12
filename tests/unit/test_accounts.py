"""append_account writes the live workbook in place, with no backup copy, then read-back."""
import zipfile
from pathlib import Path

from apply import accounts


def _xlsx(path: Path) -> None:
    ss = ("<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
          "<si><t>Company</t></si><si><t>Email</t></si><si><t>PW</t></si>"
          "<si><t>OldCo</t></si><si><t>old@example.com</t></si><si><t>x</t></si></sst>")
    sheet = ("<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
             "<dimension ref='D5:F6'/><sheetData>"
             "<row r='5' spans='4:6'><c r='D5' t='s'><v>0</v></c><c r='E5' t='s'><v>1</v></c>"
             "<c r='F5' t='s'><v>2</v></c></row>"
             "<row r='6' spans='4:6'><c r='D6' s='2' t='s'><v>3</v></c><c r='E6' t='s'><v>4</v></c>"
             "<c r='F6' s='2' t='s'><v>5</v></c></row>"
             "</sheetData></worksheet>").replace("'", '"')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", ss.replace("'", '"'))
        z.writestr("xl/styles.xml", "<styleSheet/>")


def test_append_account_updates_live_file_with_no_backup(tmp_path):
    xlsx = tmp_path / "公司账号.xlsx"
    _xlsx(xlsx)
    before = set(tmp_path.iterdir())
    row = accounts.append_account(xlsx, "L.L.Bean", "ryanzhou@andrew.cmu.edu", "secret-test-pw")
    assert row == 7
    after = set(tmp_path.iterdir())
    created = {p.name for p in after - before}
    assert created == set(), created  # no .backup-* / .bak / .tmp.xlsx left beside the live file
    assert not list(tmp_path.glob("*.backup-*"))
    assert not list(tmp_path.glob("*.bak*"))
    assert not list(tmp_path.glob("*.tmp.xlsx"))
    assert accounts.account_rows(xlsx)[-1] == ("L.L.Bean", "ryanzhou@andrew.cmu.edu")
    company, email, password_ok = accounts._read_back_account_row(xlsx, 7, "secret-test-pw")
    assert (company, email, password_ok) == ("L.L.Bean", "ryanzhou@andrew.cmu.edu", True)
    # wrong password fails the match flag without exposing the stored value
    _, _, wrong = accounts._read_back_account_row(xlsx, 7, "other")
    assert wrong is False


def test_append_account_second_row_stays_in_place(tmp_path):
    xlsx = tmp_path / "公司账号.xlsx"
    _xlsx(xlsx)
    accounts.append_account(xlsx, "One", "a@example.com", "pw-one")
    row = accounts.append_account(xlsx, "Two", "b@example.com", "pw-two")
    assert row == 8
    assert not list(tmp_path.glob("*.backup-*"))
    assert accounts.account_rows(xlsx)[-2:] == [("One", "a@example.com"), ("Two", "b@example.com")]


def test_append_account_has_no_backup_parameter():
    import inspect
    assert "backup" not in inspect.signature(accounts.append_account).parameters


def test_append_account_does_not_create_backups_folder(tmp_path):
    xlsx = tmp_path / "公司账号.xlsx"
    _xlsx(xlsx)
    accounts.append_account(xlsx, "KLA", "ryanzhou@andrew.cmu.edu", "secret-test-pw")
    assert not (tmp_path / "backups").exists()
    assert not list(tmp_path.rglob("*.backup-*"))
    assert not list(tmp_path.rglob("*.bak*"))
    assert not list(tmp_path.rglob("*.tmp.xlsx"))

