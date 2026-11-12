"""Extract visible form fields, buttons, errors and captcha/login signals from a page.

Each element gets a `data-apply-id` attribute (prefixed by a per-document token), so
keys are stable across re-reads of the same DOM and change after a full navigation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from . import rules

log = logging.getLogger(__name__)

_JS = r"""
() => {
  const W = window;
  if (!W.__applyDoc) W.__applyDoc = Math.random().toString(36).slice(2, 8);
  W.__applyCounter = W.__applyCounter || 0;
  const doc = W.__applyDoc;
  const txt = s => (s || '').replace(/\s+/g, ' ').trim();
  const idOf = el => { if (!el.dataset.applyId) el.dataset.applyId = doc + '-' + (++W.__applyCounter); return el.dataset.applyId; };
  const visible = el => {
    if (!el || !el.isConnected) return false;
    let n = el;
    while (n && n.nodeType === 1) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden') return false;
      if (n.hidden) return false;
      n = n.parentElement;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const visibleChain = el => {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || n.hidden) return false;
    }
    return true;
  };
  const labelEl = el => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l; }
    return el.closest('label');
  };
  const byIds = ids => txt((ids || '').split(/\s+/).map(i => { const e = document.getElementById(i); return e ? e.innerText : ''; }).join(' '));
  const ownLabel = el => {
    if (el.getAttribute('aria-labelledby')) { const t = byIds(el.getAttribute('aria-labelledby')); if (t) return t; }
    const l = labelEl(el); if (l && txt(l.innerText)) return txt(l.innerText);
    if (el.getAttribute('aria-label')) return txt(el.getAttribute('aria-label'));
    return '';
  };
  const containerLabel = (el, stopAt) => {
    let n = el;
    for (let lvl = 0; lvl < 4 && n && n !== document.body; lvl++) {
      let s = n.previousElementSibling;
      for (let k = 0; k < 3 && s; k++, s = s.previousElementSibling) {
        if (stopAt && stopAt.some(x => s.contains(x))) continue;
        if (/^(button|a)$/i.test(s.tagName) || s.getAttribute('role') === 'button') continue;
        if (s.querySelector('input,select,textarea') || /^(input|select|textarea)$/i.test(s.tagName)) break;
        const t = txt(s.innerText); if (t && visible(s)) return t;
      }
      n = n.parentElement;
    }
    let p = el.parentElement;
    for (let i = 0; i < 5 && p && p !== document.body; i++) {
      const cands = p.querySelectorAll('legend, label, [class*="label"], [class*="question"], [class*="title"], h3, h4, p, span');
      for (const c of cands) {
        if (c.contains(el) || (stopAt && stopAt.some(s => c.contains(s) || s.contains(c)))) continue;
        if (c.querySelector('input,select,textarea')) continue;
        const t = txt(c.innerText); if (t && visible(c)) return t;
      }
      p = p.parentElement;
    }
    return '';
  };
  const groupLabel = (els) => {
    const fs = els[0].closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && txt(lg.innerText)) return txt(lg.innerText); }
    const rg = els[0].closest('[role="radiogroup"],[role="group"]');
    if (rg) {
      if (rg.getAttribute('aria-labelledby')) { const t = byIds(rg.getAttribute('aria-labelledby')); if (t) return t; }
      if (rg.getAttribute('aria-label')) return txt(rg.getAttribute('aria-label'));
    }
    const labels = els.map(labelEl).filter(Boolean);
    return containerLabel(els[0], labels.concat(els));
  };
  const errorsNear = el => {
    const out = new Set();
    const d = el.getAttribute('aria-describedby') || el.getAttribute('aria-errormessage');
    if (d) d.split(/\s+/).forEach(i => { const e = document.getElementById(i); if (e && visible(e) && /error|invalid|required/i.test(e.className + ' ' + e.innerText)) out.add(txt(e.innerText)); });
    let p = el.parentElement;
    for (let i = 0; i < 3 && p && p !== document.body; i++) {
      p.querySelectorAll('[class*="error"],[class*="invalid"],[role="alert"],.help-block').forEach(e => { if (visible(e) && !e.querySelector('input,select,textarea') && txt(e.innerText)) out.add(txt(e.innerText)); });
      if (out.size) break;
      p = p.parentElement;
    }
    return [...out].slice(0, 3);
  };
  // value a custom select shows next to its (empty) search input, ignoring placeholder text
  const comboDisplay = el => {
    let n = el.parentElement;
    for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
      if (n.querySelector('label, [role=listbox]')) break;
      const c = n.cloneNode(true);
      c.querySelectorAll('[class*="placeholder"], [class*="Placeholder"], input, [aria-live], [class*="a11y"]').forEach(x => x.remove());
      const t = txt(c.innerText || c.textContent);
      if (t) return t;
    }
    return '';
  };
  const reqByClass = el => {
    // many ATSs mark required questions only with a CSS class on the question label (asterisk drawn via ::after)
    const own = labelEl(el);
    if (own && /required/i.test(own.className || '')) return true;
    const cont = el.closest('fieldset, [class*="fieldEntry"], [class*="field-entry"], [class*="question"]');
    return !!(cont && cont.querySelector(':scope label[class*="required"], :scope legend[class*="required"], :scope [class*="question-title"][class*="required"]'));
  };
  const isReq = (el, label) => !!(el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$|^\*|\(required\)/i.test(label) || reqByClass(el));
  const clean = l => txt((l || '').replace(/\s*[*✱]\s*$/, '').replace(/^\*\s*/, ''));
  // nearest heading before an element in document order (section of a repeatable entry, e.g. "Work Experience 2")
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,[role="heading"],[class*="section-title" i],[class*="sectionTitle"],[data-automation-id*="Header"]')]
    .filter(h => visible(h) && txt(h.innerText) && txt(h.innerText).length < 80);
  const sectionOf = el => {
    let best = '';
    for (const h of headings) {
      if (h.contains(el)) continue;
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) best = txt(h.innerText); else break;
    }
    return best;
  };
  const fields = [];
  const seenGroups = new Set();
  const els = document.querySelectorAll('input, select, textarea, [role="combobox"]:not(input), [contenteditable="true"]');
  for (const el of els) {
    const tag = el.tagName.toLowerCase();
    let type = tag === 'input' ? (el.type || 'text').toLowerCase() : tag;
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) continue;
    if (el.disabled) continue;
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex < 0 && type !== 'file') continue;  // widget helper inputs
    if (type === 'file') {
      // file inputs are often visually hidden themselves, but their container must be shown
      if (!el.parentElement || !visibleChain(el.parentElement)) continue;
      // file inputs are often visually hidden behind a styled button
      let lab = ownLabel(el);
      if (!lab || /^(attach|upload|choose( a)? file|browse|select file|drop|add file)\b/i.test(lab)) {
        const grp = el.closest('[role="group"][aria-labelledby]');
        lab = (grp && byIds(grp.getAttribute('aria-labelledby'))) || containerLabel(el) || lab || el.name || el.id || 'file upload';
      }
      fields.push({ id: idOf(el), type: 'file', label: clean(lab), required: isReq(el, lab), value: el.files && el.files.length ? el.files[0].name : '', options: [], errors: errorsNear(el), name: el.name || '', accept: el.accept || '' });
      continue;
    }
    if (type === 'radio' || type === 'checkbox') {
      const lab = labelEl(el);
      if (!visible(el) && !(lab && visible(lab))) continue;
      let name = el.name;
      let group = name ? [...document.querySelectorAll('input[type="' + type + '"][name="' + CSS.escape(name) + '"]')] : [el];
      if (type === 'checkbox' && group.length === 1) {
        // checkbox groups whose options have distinct names (e.g. Ashby): one fieldset / role=group holding only checkboxes
        const box = el.closest('fieldset, [role="group"]');
        const boxes = box ? [...box.querySelectorAll('input[type="checkbox"]')] : [];
        if (boxes.length >= 2 && boxes.length <= 30 && !box.querySelector('input:not([type="checkbox"]):not([type="hidden"]), select, textarea')) {
          group = boxes; name = 'group:' + idOf(box);
        }
      }
      if (type === 'checkbox' && group.length === 1) {
        const l = ownLabel(el) || containerLabel(el);
        fields.push({ id: idOf(el), type: 'checkbox', label: clean(l), required: isReq(el, l), value: el.checked ? 'checked' : '', options: [], errors: errorsNear(el), name: name || '' });
        continue;
      }
      if (seenGroups.has(type + ':' + name)) continue;
      seenGroups.add(type + ':' + name);
      const opts = group.filter(g => visible(g) || (labelEl(g) && visible(labelEl(g)))).map(g => ({ label: clean(ownLabel(g) || g.value), id: idOf(g), checked: g.checked }));
      const gl = groupLabel(group);
      fields.push({ id: idOf(group[0]), type: type === 'radio' ? 'radio' : 'checkbox_group', label: clean(gl), required: group.some(g => g.required) || /[*✱]\s*$/.test(gl) || reqByClass(group[0]),
        value: opts.filter(o => o.checked).map(o => o.label).join('; '), options: opts.map(o => o.label), option_ids: opts.map(o => o.id), errors: errorsNear(group[0]), name: name.startsWith('group:') ? '' : (name || '') });
      continue;
    }
    if (!visible(el)) continue;
    const role = el.getAttribute('role');
    const isCombo = role === 'combobox' || el.getAttribute('aria-autocomplete') === 'list' || el.getAttribute('aria-haspopup') === 'listbox';
    let lab = ownLabel(el) || containerLabel(el) || el.placeholder || el.name || '';
    let options = [];
    let value = '';
    // Lever (and similar) location typeaheads: plain text input + hidden selectedLocation + JS dropdown.
    // They are NOT role=combobox, so detect by class/name/sibling markers.
    const parent = el.parentElement;
    const hasSelectedLoc = !!(parent && parent.querySelector(
      'input[name="selectedLocation"], #selected-location, input[name*="selectedLocation" i], input[id*="selected-location" i]'));
    const hasLocDropdown = !!(parent && parent.querySelector(
      '.dropdown-container, .dropdown-results, .dropdown-location, [class*="dropdown-location"]'));
    const looksLikeLocInput = /\blocation-input\b/i.test(el.className || '')
      || /^(location|currentLocation|current_location)$/i.test(el.name || '')
      || /location-input/i.test(el.id || '')
      || (el.getAttribute('data-qa') || '').toLowerCase() === 'location-input';
    const labelLooksLoc = /current location|\blocation\b|所在地|地点|城市/i.test(lab);
    const isLocationAuto = tag === 'input' && !isCombo
      && (looksLikeLocInput || ((labelLooksLoc || hasSelectedLoc) && (hasSelectedLoc || hasLocDropdown)));
    if (tag === 'select') {
      type = el.multiple ? 'multiselect' : 'select';
      options = [...el.options].map(o => txt(o.text)).filter(t => t);
      value = [...el.selectedOptions].filter(o => o.value !== '').map(o => txt(o.text)).join('; ');
    } else if (isLocationAuto) {
      type = 'location_autocomplete';
      value = el.value || '';
      // surface already-selected location name from Lever's hidden JSON field when present
      if (hasSelectedLoc) {
        const h = parent.querySelector('input[name="selectedLocation"], #selected-location, input[name*="selectedLocation" i]');
        if (h && h.value) {
          try { const j = JSON.parse(h.value); if (j && j.name) value = j.name; } catch (e) { if (!value) value = h.value; }
        }
      }
    } else if (isCombo) {
      type = 'combobox';
      const lbId = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
      const lb = lbId ? document.getElementById(lbId) : null;
      if (lb) options = [...lb.querySelectorAll('[role="option"]')].map(o => txt(o.innerText)).filter(Boolean).slice(0, 80);
      value = tag === 'input' ? el.value : txt(el.innerText);
      if (!txt(value)) value = comboDisplay(el);
    } else if (tag === 'textarea') {
      type = 'textarea'; value = el.value;
    } else if (el.isContentEditable && tag !== 'input') {
      type = 'textarea'; value = txt(el.innerText);
    } else {
      value = el.value;
    }
    const extraBits = {};
    if (el.maxLength > 0) extraBits.maxlength = el.maxLength;
    if (el.getAttribute('pattern')) extraBits.pattern = el.getAttribute('pattern');
    if (isLocationAuto) extraBits.location_autocomplete = true;
    fields.push({ id: idOf(el), type, input_type: tag === 'input' ? el.type : '', label: clean(lab), required: isReq(el, lab), value: value || '',
      options, errors: errorsNear(el), name: el.name || '', placeholder: el.placeholder || '', maxlength: el.maxLength > 0 ? el.maxLength : null,
      pattern: el.getAttribute('pattern') || '', location_autocomplete: !!isLocationAuto,
      multi: type === 'combobox' && (!!el.closest('[class*="value-container--is-multi"], [aria-multiselectable="true"]') || /\[\]$/.test(el.id || '') || el.getAttribute('aria-multiselectable') === 'true'),
      date_picker: tag === 'input' && (/datepicker|input-date/i.test(String(el.className || '')) || !!el.closest('.react-datepicker-wrapper, [class*="datepicker"]') || /^pick (a )?date/i.test(el.placeholder || '')) });
  }
  // toggle-button groups (e.g. Yes/No rendered as <button aria-pressed>), usually backed by a hidden input
  const seenBtnGroups = new Set();
  document.querySelectorAll('button[aria-pressed]').forEach(btn => {
    const box = btn.parentElement;
    if (!box || seenBtnGroups.has(box) || !visible(btn)) return;
    seenBtnGroups.add(box);
    const btns = [...box.querySelectorAll(':scope > button[aria-pressed]')].filter(visible);
    if (btns.length < 2 || btns.length > 8) return;
    const lab = containerLabel(btns[0], btns) || '';
    if (!lab) return;
    const backing = box.querySelector('input');
    fields.push({ id: idOf(box), type: 'button_group', label: clean(lab), required: isReq(backing || btns[0], lab),
      value: btns.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => txt(b.innerText)).join('; '),
      options: btns.map(b => txt(b.innerText)), option_ids: btns.map(idOf), errors: errorsNear(box), name: backing ? backing.name || '' : '' });
  });
  fields.forEach(f => { const el = document.querySelector('[data-apply-id="' + f.id + '"]'); f.section = el ? sectionOf(el) : ''; });
  const pageErrors = new Set();
  document.querySelectorAll('[role="alert"], .error, .errors, .error-message, .alert-danger, [class*="error-summary"], [class*="ErrorMessage"]').forEach(e => {
    if (visible(e) && txt(e.innerText) && txt(e.innerText).length < 400) pageErrors.add(txt(e.innerText));
  });
  const buttons = [];
  document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"], a.button, a.btn, a[href*="apply"], a[class*="apply"], a[class*="Apply"]').forEach(b => {
    if (!visible(b) || b.disabled || b.getAttribute('aria-disabled') === 'true') return;
    const t = txt(b.innerText || b.value || b.getAttribute('aria-label') || '');
    if (t) buttons.push({ id: idOf(b), text: t.slice(0, 80), type: (b.getAttribute('type') || '').toLowerCase() });
  });
  const addButtons = [];
  document.querySelectorAll('button, a, [role="button"], input[type="button"]').forEach(b => {
    if (!visible(b) || b.disabled || b.getAttribute('aria-disabled') === 'true') return;
    const t = txt(b.innerText || b.value || b.getAttribute('aria-label') || '');
    if (t && t.length <= 50 && /^\s*(\+\s*)?(add|\+)\b/i.test(t))
      addButtons.push({ id: idOf(b), text: t, section: sectionOf(b) });
  });
  let captcha = false;
  let challenge = '';
  const frameUrls = [];
  const onScreen = r => r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight * 3 && r.left < innerWidth;
  document.querySelectorAll('iframe').forEach(f => {
    const src = f.src || f.getAttribute('src') || '';
    const s = src + ' ' + (f.title || '');
    if (src) frameUrls.push(src.slice(0, 200));
    if (!/captcha|turnstile|challenges\.cloudflare|captcha-delivery/i.test(s) || !visibleChain(f)) return;
    const r = f.getBoundingClientRect();
    if (/size=invisible/i.test(s) || !onScreen(r)) return;
    if (r.width > 100 && r.height > 50) captcha = true;
    // the puzzle itself (not the "I'm not a robot" checkbox): hCaptcha challenge frame, reCAPTCHA bframe, DataDome
    if (/frame=challenge|hcaptcha challenge|recaptcha\/(api2|enterprise)\/bframe|recaptcha challenge|captcha-delivery/i.test(s) && r.width > 200 && r.height > 200)
      challenge = /hcaptcha/i.test(s) ? 'hCaptcha challenge' : /recaptcha/i.test(s) ? 'reCAPTCHA challenge' : 'DataDome captcha';
  });

  const h = document.querySelector('h1, h2, [class*="step"][class*="active"], [aria-current="step"]');
  return { doc, url: location.href, title: document.title, heading: h ? txt(h.innerText).slice(0, 200) : '',
           fields, page_errors: [...pageErrors].slice(0, 10), buttons, captcha, challenge, frame_urls: frameUrls, add_buttons: addButtons,
           text: txt(document.body ? document.body.innerText : '').slice(0, 20000) };
}
"""


def _clip_label(label: str, n: int = 300) -> str:
    """Long labels put the actual question at the end (after a preamble); keep head and tail."""
    label = label or ""
    return label if len(label) <= n else label[:120] + " … " + label[-(n - 123):]


@dataclass
class Field:
    key: str
    frame_index: int
    apply_id: str
    type: str
    label: str
    required: bool
    value: str
    options: list[str]
    errors: list[str]
    name: str = ""
    input_type: str = ""
    placeholder: str = ""
    option_ids: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    section: str = ""                  # nearest heading above the field
    entry_kind: Optional[str] = None   # "experience" / "education" when inside a repeatable section
    entry_index: Optional[int] = None  # 0-based entry within that section

    @property
    def forced_pending(self) -> Optional[str]:
        return rules.forced_pending_reason(self.label)

    def for_llm(self) -> dict:
        d: dict[str, Any] = {"key": self.key, "type": self.type, "label": self.label, "required": self.required}
        if self.input_type and self.input_type not in ("text", self.type):
            d["input_type"] = self.input_type
        if self.placeholder:
            d["placeholder"] = self.placeholder
        if self.options:
            d["options"] = self.options[:80]
        if self.value:
            d["current_value"] = self.value[:300]
        if self.errors:
            d["errors"] = self.errors
        for k in ("accept", "maxlength", "pattern"):
            if self.extra.get(k):
                d[k] = self.extra[k]
        if self.extra.get("multi"):
            d["multiple"] = True
        if self.extra.get("date_picker"):
            d["date_format"] = "MM/DD/YYYY (date picker; a month-only date like May 2028 is entered as 05/01/2028)"
        for k in ():
            if self.extra.get(k):
                d[k] = self.extra[k]
        if self.entry_kind is not None:
            d["entry"] = {"section": self.entry_kind, "index": self.entry_index}
        return d


@dataclass
class Button:
    key: str
    frame_index: int
    apply_id: str
    text: str
    kind: Optional[str]  # "next" | "submit" | None


@dataclass
class AddButton:
    """'Add another' control of a repeatable section (work experience, education, ...)."""
    key: str
    frame_index: int
    apply_id: str
    text: str
    kind: Optional[str]  # "experience" | "education" | None (other repeatables are left alone)


@dataclass
class Snapshot:
    url: str
    title: str
    heading: str
    doc: str
    fields: list[Field]
    buttons: list[Button]
    page_errors: list[str]
    captcha: bool
    text: str
    challenge: str = ""                  # interactive captcha puzzle currently shown ("" if none)
    frame_urls: list[str] = field(default_factory=list)
    add_buttons: list[AddButton] = field(default_factory=list)

    @property
    def bot_block(self) -> Optional[str]:
        """Bot-protection wall in place of the form (few/no fields), else None."""
        if len([f for f in self.fields if f.type not in ("checkbox", "file")]) >= 2:
            return None
        return rules.bot_block_reason(f"{self.title} {self.text}", self.frame_urls)

    def entry_count(self, kind: str) -> int:
        idx = {f.entry_index for f in self.fields if f.entry_kind == kind and f.entry_index is not None}
        return max(idx) + 1 if idx else 0

    def add_button(self, kind: str) -> Optional[AddButton]:
        return next((b for b in reversed(self.add_buttons) if b.kind == kind), None)

    @property
    def keys(self) -> list[str]:
        return [f.key for f in self.fields]

    def field(self, key: str) -> Optional[Field]:
        return next((f for f in self.fields if f.key == key), None)

    def nav_button(self) -> Optional[Button]:
        nxt = [b for b in self.buttons if b.kind == "next"]
        if nxt:
            return nxt[-1]
        sub = [b for b in self.buttons if b.kind == "submit"]
        return sub[-1] if sub else None

    @property
    def is_final(self) -> bool:
        return not any(b.kind == "next" for b in self.buttons) and any(b.kind == "submit" for b in self.buttons)

    @property
    def login_required(self) -> bool:
        return rules.detect_login(f.input_type or f.type for f in self.fields)


def read_page(page) -> Snapshot:
    """Read the main frame plus same/cross-origin child frames that Playwright can evaluate."""
    fields: list[Field] = []
    buttons: list[Button] = []
    adds: list[AddButton] = []
    errors: list[str] = []
    captcha = False
    challenge = ""
    frame_urls: list[str] = []
    sections: list[dict] = []
    main: Optional[dict] = None
    for idx, frame in enumerate(page.frames):
        try:
            data = frame.evaluate(_JS)
        except Exception as e:  # detached / restricted frames
            log.debug("frame %s not readable: %s", idx, e)
            continue
        if idx == 0:
            main = data
        captcha = captcha or bool(data.get("captcha"))
        challenge = challenge or data.get("challenge") or ""
        frame_urls.extend(data.get("frame_urls") or [])
        if idx > 0 and re_captcha_url(frame.url):
            continue
        for f in data["fields"]:
            extra = {k: f.get(k) for k in ("accept", "maxlength", "pattern", "location_autocomplete", "date_picker", "multi") if f.get(k)}
            fields.append(Field(key=f"{idx}:{f['id']}", frame_index=idx, apply_id=f["id"], type=f["type"],
                                label=_clip_label(f["label"]), required=bool(f["required"]), value=f.get("value") or "",
                                options=f.get("options") or [], errors=f.get("errors") or [], name=f.get("name", ""),
                                input_type=f.get("input_type", ""), placeholder=f.get("placeholder", ""),
                                option_ids=f.get("option_ids") or [], extra=extra, section=f.get("section") or ""))
            sections.append({"section": f.get("section") or "", "label": f["label"], "frame": idx})
        for b in data["buttons"]:
            buttons.append(Button(key=f"{idx}:{b['id']}", frame_index=idx, apply_id=b["id"], text=b["text"],
                                  kind=rules.classify_button(b["text"])))
        for b in data.get("add_buttons") or []:
            if rules.is_add_entry_button(b["text"]):
                kind = rules.section_kind(b["text"]) or rules.section_kind(b.get("section", ""))
                adds.append(AddButton(key=f"{idx}:{b['id']}", frame_index=idx, apply_id=b["id"], text=b["text"], kind=kind))
        errors.extend(data["page_errors"])
    for f, entry in zip(fields, rules.assign_entries(sections)):
        if entry:
            f.entry_kind, f.entry_index = entry
    main = main or {"url": page.url, "title": "", "heading": "", "doc": "", "text": ""}
    frame_urls.extend(fr.url for fr in page.frames if fr.url)
    return Snapshot(url=main["url"], title=main["title"], heading=main["heading"], doc=main.get("doc", ""),
                    fields=fields, buttons=buttons, page_errors=list(dict.fromkeys(errors)), captcha=captcha,
                    text=main.get("text", ""), challenge=challenge, frame_urls=list(dict.fromkeys(frame_urls)),
                    add_buttons=adds)


def re_captcha_url(url: str) -> bool:
    import re
    return bool(re.search(r"captcha|turnstile|challenges\.cloudflare|captcha-delivery", url or "", re.I))


def locator(page, frame_index: int, apply_id: str):
    return page.frames[frame_index].locator(f'[data-apply-id="{apply_id}"]')
