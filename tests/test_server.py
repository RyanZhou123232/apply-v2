import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
import server

class LocalServerTests(unittest.TestCase):
    def test_read_list_requires_token_and_refresh_rejects_foreign_origin(self):
        previous_root = server.workflow.ROOT
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root = Path(d);server.workflow.ROOT=root
            # default function arguments were bound at import, so replace read
            # handler for the isolated test instead of using the user's DB.
            original_snapshot=server.workflow.snapshot
            server.workflow.snapshot=lambda: {'jobs':[],'last_run':None}
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{http.server_port}'
            try:
                with urllib.request.urlopen(url+'/health') as r:
                    self.assertEqual(json.load(r)['service'],'local-internship-workflow-v1')
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(url+'/api/jobs')
                with urllib.request.urlopen(urllib.request.Request(url+'/api/jobs',headers={'X-Local-Token':server.TOKEN})) as r:
                    self.assertEqual(json.load(r)['jobs'],[])
                request=urllib.request.Request(url+'/api/refresh',data=b'',headers={'Origin':'https://foreign.example','X-Local-Token':server.TOKEN},method='POST')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code,403)
            finally:
                http.shutdown();http.server_close();thread.join()
                server.workflow.snapshot=original_snapshot
                server.workflow.ROOT=previous_root

if __name__=='__main__':unittest.main()
