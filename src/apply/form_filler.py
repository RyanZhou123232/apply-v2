"""Execute planned actions with Playwright and read every value back."""
from __future__ import annotations

import json
import time
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from . import rules
from .answer_planner import PlannedAction
from .form_reader import Field, Snapshot, locator

log = logging.getLogger(__name__)
TIMEOUT = 5000


@dataclass
class FillResult:
    key: str
    label: str
    type: str
    action: str
    value: Any
    ok: bool
    actual: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            return [str(x) for x in json.loads(v)]
        except Exception:
            pass
    return [s.strip() for s in str(v).split(";") if s.strip()] if v else []


class FormFiller:
    def __init__(self, page, resume_path: str):
        self.page = page
        self.resume_path = resume_path

    def apply(self, snap: Snapshot, actions: list[PlannedAction]) -> list[FillResult]:
        results = []
        for a in actions:
            f = snap.field(a.key)
            if not f or a.action in ("skip", "pending"):
                continue
            try:
                res = self._do(f, a)
            except Exception as e:
                res = FillResult(f.key, f.label, f.type, a.action, a.value, False, note=f"error: {str(e).splitlines()[0][:200]}")
            if not res.ok:
                log.info("mismatch %s (%s): %s", f.label, f.type, res.note or res.actual)
            results.append(res)
        return results

    def add_entry(self, btn) -> bool:
        """Click a repeatable section's Add button once. True if the click went through."""
        try:
            el = locator(self.page, btn.frame_index, btn.apply_id).first
            el.scroll_into_view_if_needed(timeout=TIMEOUT)
            el.click(timeout=TIMEOUT)
            self.page.wait_for_timeout(700)
            return True
        except Exception as e:
            log.info("add-entry click failed (%s): %s", btn.text, str(e).splitlines()[0][:200])
            return False

    # ------------------------------------------------------------ actions
    def _do(self, f: Field, a: PlannedAction) -> FillResult:
        loc = locator(self.page, f.frame_index, f.apply_id)
        mk = lambda ok, actual="", note="": FillResult(f.key, f.label, f.type, a.action, a.value, ok, actual, note)

        if a.action == "upload_resume":
            loc.set_input_files(self.resume_path, timeout=TIMEOUT)
            self.page.wait_for_timeout(1500)
            name = os.path.basename(self.resume_path)
            if loc.count():
                n = loc.evaluate("e => e.files ? e.files.length : 0")
                if n:
                    return mk(True, f"{n} file(s)")
            # widgets often replace the input with a file chip after upload
            shown = self.page.frames[f.frame_index].evaluate("n => document.body.innerText.includes(n)", name)
            return mk(bool(shown), "file name shown on page" if shown else "", "" if shown else "upload not confirmed")

        if f.type in ("radio", "checkbox_group"):
            return self._choose_options(f, a, mk)

        if f.type == "button_group":
            i = rules.best_option(str(a.value), f.options)
            if i is None:
                return mk(False, note=f"option not found: {a.value!r}")
            frame = self.page.frames[f.frame_index]
            btn = frame.locator(f'[data-apply-id="{f.option_ids[i]}"]')
            if btn.get_attribute("aria-pressed") != "true":
                btn.click(timeout=TIMEOUT)
                self.page.wait_for_timeout(200)
            pressed = btn.get_attribute("aria-pressed") == "true"
            return mk(pressed, f.options[i] if pressed else "not pressed")

        if f.type == "checkbox":
            want = a.action != "uncheck"
            loc.set_checked(want, timeout=TIMEOUT, force=True)
            got = loc.is_checked()
            return mk(got == want, "checked" if got else "unchecked")

        if f.type in ("select", "multiselect"):
            targets = _as_list(a.value) if f.type == "multiselect" else [str(a.value)]
            labels = []
            for t in targets:
                i = rules.best_option(t, f.options)
                if i is None:
                    return mk(False, note=f"option not found: {t!r}")
                labels.append(f.options[i])
            loc.select_option(label=labels if f.type == "multiselect" else labels[0], timeout=TIMEOUT)
            actual = loc.evaluate("e => [...e.selectedOptions].map(o => o.text.trim()).join('; ')")
            return mk(all(rules.values_match(l, x) for l, x in zip(labels, actual.split("; "))), actual)

        if f.type == "combobox" and f.extra.get("multi"):
            return self._multi_combobox(f, a, loc, mk)
        if f.type == "combobox":
            return self._combobox(f, a, loc, mk)

        if f.type == "location_autocomplete" or f.extra.get("location_autocomplete"):
            return self._location_autocomplete(f, a, loc, mk)

        # text-like
        value = str(a.value)
        if f.extra.get("maxlength") and len(value) > f.extra["maxlength"]:
            value = value[: f.extra["maxlength"]]
        if f.extra.get("date_picker"):
            # react-datepicker style inputs only commit on a real blur and clear values they cannot parse
            value = rules.picker_date(value)
            loc.click(timeout=TIMEOUT)
            loc.fill(value, timeout=TIMEOUT)
            self.page.wait_for_timeout(200)
            loc.press("Tab", timeout=TIMEOUT)
            self.page.wait_for_timeout(400)
            actual = loc.input_value()
            return mk(bool(actual) and rules.values_match(value, actual), actual[:200],
                      "" if actual else "date picker cleared the value")
        is_ce = loc.evaluate("e => e.isContentEditable && !('value' in e)")
        if is_ce:
            loc.click(timeout=TIMEOUT)
            loc.fill(value, timeout=TIMEOUT)
            actual = loc.inner_text()
        else:
            loc.fill(value, timeout=TIMEOUT)
            loc.evaluate("e => e.dispatchEvent(new Event('blur', {bubbles: true}))")
            actual = loc.input_value()
        if not rules.values_match(value, actual) and (f.input_type in ("tel", "date", "") or f.type == "text"):
            # masked inputs: type character by character
            loc.fill("", timeout=TIMEOUT)
            loc.press_sequentially(value, delay=15, timeout=TIMEOUT * 4)
            actual = loc.inner_text() if is_ce else loc.input_value()
        return mk(rules.values_match(value, actual), actual[:200])

    def _choose_options(self, f: Field, a: PlannedAction, mk) -> FillResult:
        targets = _as_list(a.value) if f.type == "checkbox_group" else [str(a.value)]
        frame = self.page.frames[f.frame_index]
        chosen = []
        for t in targets:
            i = rules.best_option(t, f.options)
            if i is None:
                return mk(False, note=f"option not found: {t!r}")
            el = frame.locator(f'[data-apply-id="{f.option_ids[i]}"]')
            try:
                el.check(timeout=TIMEOUT)
            except Exception:
                # custom-styled inputs: click the label instead
                el.evaluate("e => { const l = (e.id && document.querySelector('label[for=\"'+CSS.escape(e.id)+'\"]')) || e.closest('label'); (l || e).click(); }")
            chosen.append(i)
        checked = [f.options[i] for i, oid in enumerate(f.option_ids)
                   if frame.locator(f'[data-apply-id="{oid}"]').is_checked()]
        ok = all(f.options[i] in checked for i in chosen)
        return mk(ok, "; ".join(checked))

    def _combobox(self, f: Field, a: PlannedAction, loc, mk) -> FillResult:
        value = str(a.value)
        frame = self.page.frames[f.frame_index]
        tag = loc.evaluate("e => e.tagName.toLowerCase()")
        opts = frame.locator('[role="option"]:visible, [class*="option"][id*="option"]:visible')
        # try: open with no text (static lists), then progressively shorter queries (async / filtered lists)
        words = value.split()
        queries = [""] + list(dict.fromkeys([value[:40], " ".join(words[:2]), words[0] if words else ""]))
        texts: list[str] = []
        chosen = None
        for q in queries:
            loc.click(timeout=TIMEOUT)
            if tag == "input":
                _clear_input(loc)
                if q:
                    loc.press_sequentially(q, delay=25, timeout=TIMEOUT * 4)
            self.page.wait_for_timeout(900 if q else 500)
            if not q and not opts.count():
                self.page.keyboard.press("ArrowDown")
                self.page.wait_for_timeout(400)
            texts = [t.strip() for t in opts.all_inner_texts()][:200]
            i = rules.best_option(value, texts) if texts else None
            if i is None and texts and "," in value:
                i = rules.best_place_option(value, texts)
            if i is not None:
                chosen = texts[i]
                opts.nth(i).click(timeout=TIMEOUT)
                break
        if chosen is None and rules.is_major_question(f.label) and rules._norm(value) != rules._norm(rules.MAJOR_FALLBACK):
            if tag == "input":
                _clear_input(loc)
            self.page.keyboard.press("Escape")
            # FORM_ANSWERS: major with no exact option -> Machine Learning
            return self._combobox(f, PlannedAction(a.key, a.action, rules.MAJOR_FALLBACK, a.reason, a.enforced), loc, mk)
        if chosen is None:
            if tag == "input":
                _clear_input(loc)
            self.page.keyboard.press("Escape")
            return mk(False, note=f"no matching option for {value!r}; options seen: {texts[:25]}")
        self.page.wait_for_timeout(300)
        actual = loc.evaluate(_COMBO_VALUE_JS) if loc.count() else ""
        a_, c_ = actual.strip().lower(), chosen.lower()
        ok = rules.values_match(chosen, actual) or (bool(a_) and (c_ in a_ or a_ in c_))
        return mk(ok, actual[:200])


    def _multi_combobox(self, f: Field, a: PlannedAction, loc, mk) -> FillResult:
        """react-select multi (Greenhouse): pick each target in turn; verify every chip is shown."""
        targets = [t for t in _as_list(a.value) if str(t).strip()]
        frame = self.page.frames[f.frame_index]
        chips_js = """e => { let n = e; for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
            const c = n.querySelectorAll('[class*="multi-value__label"], [class*="multiValue"] [class*="label"], [class*="chip"] [class*="label"], [class*="tag"]');
            if (c.length) return [...c].map(x => (x.innerText || '').trim()).filter(Boolean); } return []; }"""
        notes = []
        for t in targets:
            have = loc.evaluate(chips_js) if loc.count() else []
            if any(rules.values_match(t, h) or t.lower() in h.lower() for h in have):
                continue
            r = self._combobox(f, PlannedAction(a.key, a.action, t, a.reason, a.enforced), loc, mk)
            if not r.ok and r.note:
                notes.append(r.note[:120])
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        have = loc.evaluate(chips_js) if loc.count() else []
        missing = [t for t in targets if not any(rules.values_match(t, h) or t.lower() in h.lower() or h.lower() in t.lower() for h in have)]
        return mk(not missing and bool(have), "; ".join(have)[:200], ("missing: " + "; ".join(missing) + " " + " | ".join(notes))[:300] if missing else "")

    def _location_autocomplete(self, f: Field, a: PlannedAction, loc, mk) -> FillResult:
        """Type into a JS location typeahead (e.g. Lever), wait past Loading, pick a suggestion."""
        value = str(a.value).strip()
        frame = self.page.frames[f.frame_index]
        query = value.split(",")[0].strip() or value
        loc.click(timeout=TIMEOUT)
        loc.fill("", timeout=TIMEOUT)
        loc.press_sequentially(query, delay=30, timeout=TIMEOUT * 4)

        suggestion_sel = (
            ".dropdown-location:visible, "
            ".dropdown-results .dropdown-location:visible, "
            "[class*='dropdown-location']:visible, "
            "[role='option']:visible, "
            ".pac-item:visible, "
            "[class*='suggest'] li:visible, "
            "[class*='typeahead'] li:visible"
        )
        loading_sel = (
            ".dropdown-loading-results:visible, "
            "[class*='dropdown-loading']:visible, "
            "[class*='loading-results']:visible"
        )

        texts: list[str] = []
        deadline = time.time() + 8.0
        while time.time() < deadline:
            loading = False
            try:
                loading = frame.locator(loading_sel).count() > 0
            except Exception:
                loading = False
            try:
                body = frame.locator(".dropdown-container:visible, [class*='dropdown-container']:visible")
                if body.count():
                    t = (body.first.inner_text(timeout=500) or "").strip().lower()
                    if t == "loading" or t.startswith("loading\n") or t.startswith("loading"):
                        # still only the loading row
                        if "pittsburgh" not in t and "," not in t:
                            loading = True
            except Exception:
                pass
            opts = frame.locator(suggestion_sel)
            try:
                n = opts.count()
            except Exception:
                n = 0
            if n and not loading:
                raw = [t.strip() for t in opts.all_inner_texts()]
                texts = [t for t in raw if t and t.lower() not in (
                    "loading", "no location found. try entering a different location")]
                if texts:
                    break
            self.page.wait_for_timeout(250)

        if not texts:
            self.page.keyboard.press("Escape")
            return mk(False, note=f"location suggestions never appeared for {value!r}")

        i = rules.best_option(value, texts)
        if i is None:
            i = rules.best_place_option(value, texts)
        if i is None:
            i = rules.best_option(query, texts)
        if i is None:
            self.page.keyboard.press("Escape")
            return mk(False, note=f"no matching location for {value!r}; seen: {texts[:15]}")

        chosen = texts[i]
        frame.locator(suggestion_sel).nth(i).click(timeout=TIMEOUT)
        self.page.wait_for_timeout(400)

        try:
            actual = loc.input_value(timeout=TIMEOUT)
        except Exception:
            actual = ""

        hidden_val = loc.evaluate("""e => {
          const p = e.closest('.application-field') || e.parentElement;
          if (!p) return null;
          const h = p.querySelector('#selected-location, input[name="selectedLocation"], input[name*="selectedLocation" i]');
          return h ? (h.value || '') : null;
        }""")
        stuck = False
        if hidden_val is not None:
            stuck = bool(str(hidden_val).strip())
            if stuck and not (actual or "").strip():
                try:
                    actual = json.loads(hidden_val).get("name", "") or actual
                except Exception:
                    pass
        if not stuck:
            a_, c_ = (actual or "").strip().lower(), chosen.lower()
            stuck = rules.values_match(chosen, actual) or (bool(a_) and (c_ in a_ or a_ in c_))
        shown = (actual or "").strip() if (actual or "").strip() else chosen
        if not stuck:
            return mk(False, shown[:200], note="location suggestion click did not stick (selectedLocation empty)")
        return mk(True, shown[:200])



def _clear_input(loc) -> None:
    """Clear a combobox search input only if it has text: on react-select, clearing an EMPTY input acts like
    Backspace and removes the last selected chip (multi-select)."""
    try:
        if loc.input_value():
            loc.fill("", timeout=TIMEOUT)
    except Exception:
        loc.fill("", timeout=TIMEOUT)


# Read what a combobox displays: its own value, else the nearest ancestor's text minus the label.
_COMBO_VALUE_JS = """e => {
  if (e.value && e.value.trim()) return e.value.trim();
  let n = e.parentElement;
  for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
    if (n.querySelector('label') || n.querySelector('[role=listbox]')) break;
    const t = (n.innerText || '').trim();
    if (t) return t;
  }
  return (e.innerText || '').trim();
}"""
