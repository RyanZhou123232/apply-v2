# apply-v2 · P1 dynamic form filler

One job URL + one resume → fill the application form page by page with Playwright, with OpenAI deciding the answers.
P1 stops before Submit (`submit=False`). The result JSON carries a status plus Ryan's tracking status (已提交 / 待决定 / 未提交).

## Modules (src/apply/)
| module | input | output |
|---|---|---|
| form_reader.py | Playwright page | Snapshot: fields (key, label, type, options, required, value, errors), buttons (next/submit), page errors, captcha flag |
| answer_planner.py | Snapshot + all_info + FORM_ANSWERS + resume text + job context + salary | one action per field (fill/select/check/uncheck/upload_resume/pending/skip). Code forces AI-use and SSN questions to pending and fills salary fields itself |
| form_filler.py | page + actions | per-field result with the value read back, ok/mismatch |
| runner.py | url, resume, role category, submit flag | RunResult JSON: status, ryan_status, pending_items, filled, pages, errors, screenshot, llm usage/cost |
| rules.py | — | pure rules: salary choice and unit conversion, AI/SSN detection, status mapping, page-advance detection, button classification, bot-wall / submit-rejection patterns, the three allowed experience entries and repeatable-section entry assignment |
| browser.py | Playwright instance | the browser context: headed real Chrome on a persistent profile (`/home/box/.apply-v2-profile`, override `APPLY_PROFILE_DIR`), en-US, America/New_York, 1440x900, no `--enable-automation`; one run at a time (file lock) |
| email_codes.py | submit time | email verification code read from Gmail (read-only) |

Status → Ryan: READY_TO_SUBMIT/PENDING → 待决定; SUBMITTED → 已提交 (with confirmation screenshot); FAILED/CAPTCHA/LOGIN_REQUIRED/BLOCKED → 未提交 (with a `debug-<status>.png` screenshot). `reason` holds a one-line why.

### Bot protection and captchas (unattended)
- BLOCKED: a bot wall instead of the form (DataDome, Cloudflare "Just a moment", Imperva, "Access denied"), or the employer rejects the submit ("There was an error verifying your application", "error processing your application").
- CAPTCHA: an interactive hCaptcha / reCAPTCHA puzzle is shown.
- Either ends the run at once. Submit is clicked at most once; after a click `resubmit_safe` is false, so never re-run that job automatically.
- `--captcha-wait N` (attended only) waits N seconds for a human to solve a puzzle after Submit.

### Repeatable sections (work experience / education)
The reader tags fields inside "Work Experience" / "Education" style sections with an entry index and finds their Add buttons.
The runner clicks Add until there are 3 experience entries (PwC, SDIC Securities, CMU HCII, in that order) and 1 education entry (CMU);
other repeatables (websites, languages) are left alone. Company fields in entry i are set in code from `rules.EXPERIENCE_ENTRIES[i]`; Lucina or other personal projects are never entered.

## Run
```
cd src
python -m apply.runner --url URL --resume "path/to/resume.pdf" --role-category SWE [--headless] [--legacy-browser] [--run-dir DIR] \
   [--salary-hint '{"amount":21,"unit":"hour","source":"Levels.fyi"}'] [--jd-pay '{...}'] [--all-info PATH] [--out result.json]
```
- OpenAI key: env `OPENAI_API_KEY`, or the file named by `APPLY_OPENAI_KEY_FILE`. Model: `OPENAI_MODEL` (default gpt-5.4); `OPENAI_REASONING_EFFORT` (default low).
- all_info: `--all-info` > env `APPLY_ALL_INFO` > `<project>/reference doc/all info.txt`. Rules file: `memory/FORM_ANSWERS.md`.
- `--submit` really clicks Submit. Without it, a request guard also blocks anything that looks like a submission (dry run).
- Browser: headed persistent Chrome by default. On Linux it needs `DISPLAY` (or `APPLY_DISPLAY`); without one it falls back to headless with a warning.
  `--headless` hides the window; `--legacy-browser` uses the old fresh Chromium context. `APPLY_BROWSER_CHANNEL=chromium` forces the bundled Chromium.

## Tests
Test code is throwaway and lives only in the box copy (`/workspace/apply-v2/tests`); it is not synced to this folder.
```
pytest tests/unit                       # no network
RUN_INTEGRATION=1 pytest tests          # + real OpenAI and headless Chromium against tests/integration/mock_forms
```

## Excel tracking
Updates to `tracking/tech投递2027.xlsx` and `tracking/公司账号.xlsx` must be written in place on Ryan's PC (see `docs/SYNC_README.md`, `apply.tracker_write`, `apply.accounts.append_account`). Never copy those workbooks to the box to edit them, and never write a backup copy. Passwords in `公司账号.xlsx` are never printed.
