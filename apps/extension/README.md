# Scout Autofill (Chrome extension)

Fills job application forms (Greenhouse, Lever, Ashby and most plain forms) with facts from your Scout
profile, outlines every field it touched, and leaves the rest to you. **It never presses Submit.**

## Install (2 minutes, once)

1. In Scout, open **Resume** and click **Autofill profile** (downloads `scout-autofill-profile.json`).
2. In Chrome, open `chrome://extensions`, switch on **Developer mode** (top right), click **Load unpacked**
   and choose this folder (`apps/extension`).
3. Click the puzzle icon, pin **Scout Autofill**, open its **Options**: import the profile file, check the
   fields (e.g. your surname, your city), pick the resume PDF to attach, and **Save**.

## Use

Open a job's application page, click the Scout icon, then **Fill this page**.
- Green outline: filled from your profile. Check it.
- Amber outline: your call (work authorisation, visa, salary, notice period, gender and other personal
  questions, declarations and consent boxes, cover letters, written answers). Never filled.
- **Undo fill** in the panel puts the form back as it was.

It stops without filling anything on pages with a CAPTCHA, a login or a one-time code, and it never runs on
LinkedIn or Wellfound. It only runs when you click the button (no access to any site otherwise), and your
profile stays in this browser.

## Tests

    node --test apps/extension/test/                          # field matching rules
    uv run --with playwright python apps/extension/test/browser_test.py   # real Chromium, test forms
