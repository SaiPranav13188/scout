/*
 * Scout Autofill content script. Injected only when I click "Fill this page" in the popup.
 *
 * Rules (docs/PLAN.md, agreed 2026-11-06):
 *   - fills plain facts from my saved profile and outlines every field it touched (green);
 *   - questions that are my call (work authorisation, salary, demographics, declarations, written
 *     answers) are never filled, only outlined (amber);
 *   - stops completely on CAPTCHA, OTP or login pages;
 *   - never clicks anything, so it can never submit a form;
 *   - never runs on LinkedIn or Wellfound.
 */
;(async function () {
  "use strict"
  const F = globalThis.ScoutFields
  const PANEL_ID = "scout-autofill-panel"
  const GREEN = "2px solid #16a34a"
  const AMBER = "2px dashed #d97706"
  const BLUE = "2px solid #2563eb"  // filled from my saved answers

  function report(result) {
    try { chrome.runtime.sendMessage({ type: "scout-autofill-result", result }) } catch (_) { /* popup closed */ }
    return result
  }

  if (F.blockedSite(location.hostname)) {
    return report({ status: "blocked", message: "Scout never fills LinkedIn or Wellfound forms. Apply there yourself." })
  }

  // Stop on CAPTCHA, login and OTP pages: those are always for me to do.
  const captcha = document.querySelector(
    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], iframe[src*='challenges.cloudflare'], .g-recaptcha, .h-captcha, .cf-turnstile")
  const password = document.querySelector("input[type='password']")
  const otp = document.querySelector("input[autocomplete='one-time-code']")
  if (captcha || password || otp) {
    const what = captcha ? "a CAPTCHA" : password ? "a login" : "a one-time code"
    showPanel({ filled: [], yours: [], stopped: `This page has ${what}. Scout stopped; please do this step yourself.` })
    return report({ status: "stopped", message: `Stopped: the page has ${what}.` })
  }

  const { profile, resumeFile } = await chrome.storage.local.get(["profile", "resumeFile"])
  if (!profile) return report({ status: "no-profile", message: "Import your profile first (extension options)." })

  function labelFor(el) {
    const parts = []
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
      if (l) parts.push(l.innerText)
    }
    const wrap = el.closest("label")
    if (wrap) parts.push(wrap.innerText)
    const aria = el.getAttribute("aria-label")
    if (aria) parts.push(aria)
    const by = el.getAttribute("aria-labelledby")
    if (by) by.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) parts.push(n.innerText) })
    if (!parts.length) {
      // Greenhouse / Lever / Ashby put the question text in a nearby container.
      const box = el.closest(".field, .application-question, .application-field, [class*='question'], [class*='Field'], li, fieldset")
      const q = box && box.querySelector("label, legend, .application-label, [class*='label'], [class*='Label']")
      if (q && q !== el) parts.push(q.innerText)
    }
    return parts.join(" ").slice(0, 200)
  }

  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype
      : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value)  // works with React-controlled inputs
    el.dispatchEvent(new Event("input", { bubbles: true }))
    el.dispatchEvent(new Event("change", { bubbles: true }))
  }

  function attachFile(el, file) {
    const bytes = Uint8Array.from(atob(file.base64), (c) => c.charCodeAt(0))
    const dt = new DataTransfer()
    dt.items.add(new File([bytes], file.name, { type: "application/pdf" }))
    el.files = dt.files
    el.dispatchEvent(new Event("input", { bubbles: true }))
    el.dispatchEvent(new Event("change", { bubbles: true }))
  }

  const answers = profile.answers || null
  const pageText = [document.title,
    ...[...document.querySelectorAll("[class*='location' i], [data-qa*='location' i], .posting-categories, header, h1, h2")]
      .slice(0, 30).map((n) => n.innerText),
    (document.body.innerText || "").slice(0, 6000)].join(" \n ")
  const city = answers ? F.jobCity(pageText, answers.relocate_cities) : null
  const filled = [], answered = [], yours = [], undo = []

  function setChecked(el, on) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked").set.call(el, on)
    el.dispatchEvent(new Event("input", { bubbles: true }))
    el.dispatchEvent(new Event("change", { bubbles: true }))
  }

  function groupQuestion(radio) {
    const fs = radio.closest("fieldset")
    const legend = fs && fs.querySelector("legend")
    if (legend) return legend.innerText
    const box = radio.closest(".field, .application-question, .application-field, [class*='question'], [role='radiogroup'], li")
    const q = box && box.querySelector("label:not(:has(input)), legend, .application-label, [class*='label'], [class*='Label']")
    return q ? q.innerText : radio.getAttribute("aria-label") || radio.name || ""
  }
  const fields = [...document.querySelectorAll("input, select, textarea")].filter((el) => {
    if (el.disabled || el.readOnly) return false
    const r = el.getBoundingClientRect()
    return el.type === "file" || (r.width > 0 && r.height > 0)
  })

  const radioGroups = new Map()
  for (const el of fields) {
    if (el.type === "radio") {
      const key = el.name || groupQuestion(el)
      if (!radioGroups.has(key)) radioGroups.set(key, [])
      radioGroups.get(key).push(el)
      continue
    }
    const label = labelFor(el)
    const verdict = F.classify({
      label, name: el.name, id: el.id, placeholder: el.placeholder, autocomplete: el.autocomplete,
      type: el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : el.type, tag: el.tagName,
    })
    const short = (label || el.name || el.id || "field").replace(/\s+/g, " ").trim().slice(0, 60)
    if (verdict.kind === "yours" && verdict.answer && !(el.value && el.value.trim())) {
      const inputType = el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : el.type
      const value = F.resolveAnswer(verdict.answer, answers, { question: label, inputType, city })
      if (value) {
        if (el.tagName === "SELECT") {
          const idx = F.matchChoice([...el.options].map((o) => o.text), value)
          if (idx >= 0) {
            undo.push({ el, value: el.value })
            setValue(el, el.options[idx].value)
            el.style.outline = BLUE
            el.dataset.scout = "filled"
            answered.push(`${short}: ${el.options[idx].text.slice(0, 40)}`)
            continue
          }
        } else if (el.type !== "checkbox") {
          undo.push({ el, value: el.value })
          setValue(el, value)
          el.style.outline = BLUE
          el.dataset.scout = "filled"
          answered.push(`${short}: ${value.slice(0, 40)}`)
          continue
        }
      }
    }
    if (verdict.kind === "yours") {
      el.style.outline = AMBER
      el.dataset.scout = "yours"
      yours.push(`${short} (${verdict.reason})`)
      continue
    }
    if (verdict.kind !== "fill") continue
    if (verdict.key === "resume_file") {
      if (resumeFile && el.files && el.files.length === 0) {
        undo.push({ el, file: true })
        attachFile(el, resumeFile)
        el.style.outline = GREEN
        el.dataset.scout = "filled"
        filled.push(`${short}: ${resumeFile.name}`)
      }
      continue
    }
    const value = verdict.key === "location"
      ? city || (answers && answers.home_city) || profile.location
      : profile[verdict.key]
    if (!value) continue
    if (el.value && el.value.trim()) continue  // never overwrite something already there
    if (el.tagName === "SELECT") {
      const idx = F.matchOption([...el.options].map((o) => o.text), value)
      if (idx < 0) { el.style.outline = AMBER; yours.push(`${short} (pick "${value}" yourself)`); continue }
      undo.push({ el, value: el.value })
      setValue(el, el.options[idx].value)
    } else {
      undo.push({ el, value: el.value })
      setValue(el, String(value))
    }
    el.style.outline = GREEN
    el.dataset.scout = "filled"
    filled.push(`${short}: ${String(value).slice(0, 40)}`)
  }

  for (const group of radioGroups.values()) {
    const question = groupQuestion(group[0])
    const verdict = F.classify({ label: question, type: "radio" })
    const short = question.replace(/\s+/g, " ").trim().slice(0, 60) || "choice"
    const labels = group.map((r) => (labelFor(r) || r.value || "").replace(/\s+/g, " ").trim())
    const value = verdict.answer ? F.resolveAnswer(verdict.answer, answers, { question, inputType: "radio", city }) : null
    const idx = value ? F.matchChoice(labels, value) : -1
    if (idx >= 0 && !group.some((r) => r.checked)) {
      undo.push({ el: group[idx], radio: true })
      setChecked(group[idx], true)
      group.forEach((r) => { r.style.outline = BLUE; r.dataset.scout = "filled" })
      answered.push(`${short}: ${labels[idx].slice(0, 40)}`)
    } else if (!group.some((r) => r.checked)) {
      group.forEach((r) => { r.style.outline = AMBER; r.dataset.scout = "yours" })
      yours.push(`${short} (${verdict.reason})`)
    }
  }

  globalThis.__scoutUndo = () => {
    for (const u of undo) {
      if (u.file) { u.el.value = ""; u.el.dispatchEvent(new Event("change", { bubbles: true })) }
      else if (u.radio) setChecked(u.el, false)
      else setValue(u.el, u.value)
      u.el.style.outline = ""
      delete u.el.dataset.scout
    }
    document.querySelectorAll("[data-scout='yours']").forEach((el) => { el.style.outline = ""; delete el.dataset.scout })
    document.getElementById(PANEL_ID)?.remove()
  }
  showPanel({ filled, answered, yours })
  return report({ status: "filled", filled: filled.length, answered: answered.length, yours: yours.length })

  function showPanel({ filled, answered = [], yours, stopped }) {
    document.getElementById(PANEL_ID)?.remove()
    const box = document.createElement("div")
    box.id = PANEL_ID
    box.setAttribute("role", "status")
    box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:340px;max-height:60vh;" +
      "overflow:auto;background:#111827;color:#f9fafb;font:13px/1.45 system-ui,sans-serif;border-radius:12px;" +
      "padding:14px 16px;box-shadow:0 10px 30px rgba(0,0,0,.35)"
    const h = (tag, text, css = "") => { const n = document.createElement(tag); n.textContent = text; n.style.cssText = css; return n }
    box.append(h("div", "Scout Autofill", "font-weight:600;margin-bottom:6px"))
    if (stopped) box.append(h("div", stopped, "color:#fbbf24"))
    else {
      box.append(h("div", `Filled ${filled.length} field${filled.length === 1 ? "" : "s"} (green). Check each one.`))
      if (answered.length) box.append(h("div", `Answered ${answered.length} question${answered.length === 1 ? "" : "s"} from your saved answers (blue).`, "margin-top:4px;color:#93c5fd"))
      if (city) box.append(h("div", `Job city: ${city}`, "color:#9ca3af"))
      if (yours.length) box.append(h("div", `${yours.length} question${yours.length === 1 ? " is" : "s are"} yours to answer (amber):`, "margin-top:6px;color:#fbbf24"))
      const ul = document.createElement("ul")
      ul.style.cssText = "margin:4px 0 0 16px;padding:0"
      yours.slice(0, 12).forEach((y) => ul.append(h("li", y)))
      box.append(ul)
      box.append(h("div", "Scout never presses Submit. Review the form, then submit it yourself.", "margin-top:8px;color:#9ca3af"))
      const undoBtn = h("button", "Undo fill", "margin-top:10px;margin-right:8px;background:#374151;color:#fff;border:0;border-radius:6px;padding:5px 10px;cursor:pointer")
      undoBtn.type = "button"
      undoBtn.addEventListener("click", () => globalThis.__scoutUndo())
      box.append(undoBtn)
    }
    const close = h("button", "Close", "margin-top:10px;background:transparent;color:#9ca3af;border:1px solid #4b5563;border-radius:6px;padding:5px 10px;cursor:pointer")
    close.type = "button"
    close.addEventListener("click", () => box.remove())
    box.append(close)
    document.body.append(box)
  }
})()
