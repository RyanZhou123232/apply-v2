import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import workflow as w

class DiscoveryTests(unittest.TestCase):
    def test_html_table_preserves_official_application(self):
        s = '''<table><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
        <tr><td><a href="https://example.org">A</a></td><td>Software Engineer Intern</td><td>NYC</td>
        <td><a href="https://example.org/job/12?utm_source=test"><img alt="Apply"></a><a href="https://simplify.jobs/p/abc"><img></a></td><td>1d</td></tr>
        <tr><td>↳</td><td>Data Engineer Intern</td><td>Austin, TX</td><td><a href="https://example.org/job/13">Apply</a></td><td>0d</td></tr></table>'''
        rows = w.parse_feed(s)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['apply_url'],'https://example.org/job/12')
        self.assertEqual(rows[1]['company'],'A')

    def test_markdown_links_and_html_posting_cells(self):
        text = '''| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| **[A](https://a.com)** | **[AI Intern (2027)](https://jobright.ai/jobs/info/abc?utm_source=x)** | USA | | 0d |
| ↳ | ML Intern | USA | <a href="https://a.com/job/2"><img alt="Apply"></a> | 1d |'''
        rows = w.parse_feed(text)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['title'],'AI Intern (2027)')
        self.assertEqual(rows[1]['apply_url'],'https://a.com/job/2')

    def test_job_identity_retains_requisition_and_drops_tracking(self):
        self.assertEqual(w.url_key('https://job-boards.greenhouse.io/a/jobs/123?utm_source=x'),w.url_key('https://a.com/careers?gh_jid=123'))
        self.assertNotEqual(w.url_key('https://a.com/jobs?id=1'),w.url_key('https://a.com/jobs?id=2'))
        self.assertEqual(w.url_key('https://jobs.ashbyhq.com/a/abc/application'),w.url_key('https://jobs.ashbyhq.com/a/abc'))

    def test_vansh_application_header(self):
        rows = w.parse_feed('| Company | Role | Location | Application/Link | Date Posted |\n|---|---|---|---|---|\n| A | AI Intern | USA | <a href="https://go.vansh.nyc/abc?s=y"><img></a> | Nov 01 |')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['company'],'A')

    def test_sqlite_dedup_preserves_state_and_first_seen(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            c = w.db(Path(d))
            job = dict(company='A',title='Software Intern',location='NYC',apply_url='https://a.com/job/1',listed_age='1d',closed=False)
            with c:
                self.assertTrue(w.ingest(c,job,'source1','2026-11-01T11:00:00+00:00'))
                c.execute("UPDATE jobs SET status='manual', reason='verification required'")
                self.assertFalse(w.ingest(c,job,'source2','2026-11-01T23:00:00+00:00'))
            row=c.execute('SELECT * FROM jobs').fetchone()
            self.assertEqual(row['status'],'manual')
            self.assertEqual(row['first_seen'],'2026-11-01T11:00:00+00:00')
            self.assertEqual(c.execute('SELECT count(*) FROM sightings').fetchone()[0],2)
            c.close()

    def test_history_different_role_not_excluded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            c=w.db(Path(d))
            c.execute('INSERT INTO history VALUES (?,?,?,?,?,?)',('1','A','Software Intern','https://a.com/job/1','Applied',6))
            self.assertIsNone(w.historical_match(c,dict(company='A',title='Data Intern',apply_url='https://a.com/job/2')))
            self.assertEqual(w.historical_match(c,dict(company='A',title='Software Intern',apply_url='https://a.com/job/2'))[0],'duplicate_check')
            self.assertEqual(w.historical_match(c,dict(company='A',title='Software Intern',apply_url='https://a.com/job/1'))[0],'excluded')
            c.close()

    def test_writer_context_excludes_sensitive_and_archive_sections(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);(root/'reference doc').mkdir()
            (root/'reference doc'/'all info.txt').write_text('[01 IDENTITY]\nSECRET_ADDRESS\n[02 EDUCATION]\nBS\n[03 IMMIGRATION]\nF-1\n[06 EXPERIENCE]\nRAG\n[08 APPLICATION]\nSENSITIVE_EEO\n[10 HISTORICAL]\nOLD_SCORE\n',encoding='utf-8')
            facts=w.relevant_facts(root)
            self.assertIn('BS',facts);self.assertIn('RAG',facts)
            for secret in ('SECRET_ADDRESS','SENSITIVE_EEO','OLD_SCORE'):
                self.assertNotIn(secret,facts)

    def test_excel_history_read_only(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);(root/'config').mkdir();path=root/'history.xlsx'
            wb=Workbook();ws=wb.active
            ws['D6']='A';ws['E6']='https://a.com/jobs/1';ws['F6']='AI Intern';ws['L6']='Applied';wb.save(path);wb.close()
            before=path.read_bytes()
            (root/'config'/'runtime.json').write_text(json.dumps({'application_excel':str(path)}))
            self.assertEqual(w.import_history(root),1)
            self.assertEqual(path.read_bytes(),before)

    def test_lever_urls_are_dropped_at_parse_and_ingest(self):
        self.assertTrue(w.is_lever_url('https://jobs.lever.co/zoox/abc/apply'))
        self.assertTrue(w.is_lever_url('https://jobs.eu.lever.co/acme/xyz'))
        self.assertFalse(w.is_lever_url('https://job-boards.greenhouse.io/a/jobs/123'))
        self.assertFalse(w.is_lever_url('https://example.com/jobs/lever-team'))
        text = (
            '| Company | Role | Location | Application | Age |\n'
            '|---|---|---|---|---|\n'
            '| L | SWE Intern | NYC | <a href="https://jobs.lever.co/co/jid/apply">Apply</a> | 0d |\n'
            '| B | SWE Intern | NYC | <a href="https://jobs.lever.co/co/jid2">Lever</a>'
            '<a href="https://job-boards.greenhouse.io/b/jobs/99">GH</a> | 1d |\n'
            '| C | Data Intern | NYC | <a href="https://jobs.eu.lever.co/c/eee">EU</a> | 2d |\n'
        )
        rows = w.parse_feed(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['company'], 'B')
        self.assertIn('greenhouse.io', rows[0]['apply_url'])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            c = w.db(Path(d))
            lever = dict(company='L', title='Software Intern', location='NYC',
                         apply_url='https://jobs.lever.co/co/jid/apply', listed_age='0d', closed=False)
            keep = dict(company='A', title='Software Intern', location='NYC',
                        apply_url='https://job-boards.greenhouse.io/a/jobs/123', listed_age='0d', closed=False)
            with c:
                self.assertFalse(w.ingest(c, lever, 'src', '2026-11-10T12:00:00+00:00'))
                self.assertTrue(w.ingest(c, keep, 'src', '2026-11-10T12:00:00+00:00'))
            self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
            self.assertNotIn('lever', c.execute('SELECT apply_url FROM jobs').fetchone()[0])
            c.close()

if __name__ == '__main__': unittest.main()
