/*
 * Scout Autofill: which profile value (if any) a form field asks for. Pure functions, no DOM access,
 * so they can be unit-tested with `node --test`. Loaded before content.js in the page.
 *
 * Three outcomes for a field:
 *   { kind: "fill", key }      a plain fact from my profile (name, email, school...)
 *   { kind: "yours", reason }  a question that is my call: never filled, highlighted for me
 *   { kind: "skip" }           not something Scout knows about; left alone
 */
;(function (root) {
  "use strict"

  // Questions Scout must never answer for me (legal declarations, demographics, money, consent).
  const MY_CALL = [
    [/authori[sz]ed to work|work authori[sz]ation|right to work|eligible to work|work permit/, "work authorisation"],
    [/sponsor|visa|immigration/, "visa / sponsorship"],
    [/gender|pronoun|sex\b|sexual orientation|race|ethnic|caste|religion|veteran|disabilit|marital/, "personal / demographic"],
    [/salary|compensation|\bctc\b|expected pay|pay expectation|current pay|stipend/, "salary"],
    [/notice period|start date|join(ing)? date|when can you start|availability/, "notice / start date"],
    [/relocat/, "relocation"],
    [/certify|declar|attest|acknowledg|consent|i agree|agree to|terms|privacy|policy|gdpr|data processing/, "legal declaration"],
    [/criminal|convict|background check|drug test/, "legal declaration"],
    [/how did you hear|referr(ed|al)|who referred/, "how you heard about the job"],
    [/cover letter|why (do you|are you|this|us)|tell us about|describe|motivation|anything else/, "written answer"],
    [/current (employer|company)|previous (employer|company)/, "employment history"],
  ]

  // Plain facts. Order matters: the first match wins.
  const FACTS = [
    ["linkedin", /linked\s?in/],
    ["github", /git\s?hub/],
    ["portfolio", /portfolio|personal (web)?site|\bwebsite\b|\bblog\b|other (link|url|website)/],
    ["email", /e-?mail/],
    ["phone", /phone|mobile|contact (number|no)|whats\s?app/],
    ["graduation_year", /graduat(ion|ing) (year|date)|year of (graduation|passing)|passing year|\bbatch\b|expected graduation/],
    ["school", /school|universit|college|institut/],
    ["discipline", /discipline|major|field of study|speciali[sz]ation|branch|stream/],
    ["degree", /\bdegree\b|qualification/],
    ["gpa", /\bc?gpa\b|grade point|percentage/],
    ["years_experience", /(total |years of )(professional |work |relevant )?experience|experience \(years\)|years experience/],
    ["first_name", /first name|given name|forename|^first$/],
    ["last_name", /last name|surname|family name|^last$/],
    ["full_name", /^(your )?(full |legal )?name(?! of)\b|full name|legal name|candidate name|applicant name/],
    ["location", /current (city|location)|\bcity\b|where are you (based|located)|^location\b/],
  ]

  // Questions I answered once (2026-11-07). Filled from profile.answers and outlined blue; anything the
  // answer doesn't cover (e.g. work authorisation for another country) stays for me.
  const ANSWERS = [
    ["work_auth", /authori[sz]ed to work|work authori[sz]ation|right to work|eligible to work|legally (able|permitted|allowed) to work/],
    ["sponsorship", /sponsor|\bvisa\b/],
    ["relocate", /relocat/],
    ["notice_period", /notice period/],
    ["start_date", /start date|join(ing)? date|when can you (start|join)|earliest.{0,20}(start|join)|availability to (start|join)/],
    ["current_ctc", /current (ctc|salary|compensation|pay|package)/],
    ["expected_ctc", /expected (ctc|salary|compensation|pay|package)|salary expectation|desired (salary|compensation|pay)|ctc expectation|expected annual/],
    ["backlogs", /backlog|arrear/],
    ["dob", /date of birth|\bdob\b|birth ?date|birthday/],
    ["gender", /\bgender\b|^sex\b|\bsex$/],
    ["disability", /disabilit|differently abled|pwd\b/],
    ["heard_from", /how did you (hear|find|learn|come across)|where did you (hear|find|see)|source of (this )?application/],
    ["languages", /languages? (you )?(speak|known|spoken)|languages known|which languages/],
    ["job_types", /open to (internship|part[- ]?time|full[- ]?time)|employment type(s)? (you|are you)|type of (role|employment) (you|are you)/],
    ["work_mode", /work (mode|arrangement|setting|location) preference|preferred (work )?(mode|arrangement)|remote.{0,20}hybrid|on-?site.{0,20}(remote|hybrid)/],
    ["tenth", /\b10th\b|\btenth\b|\bssc\b|secondary school|class (x|10)\b|matriculation/],
    ["twelfth", /\b12th\b|\btwelfth\b|intermediate|\bhsc\b|higher secondary|class (xii|12)\b|pre-?university|\bpuc\b/],
  ]
  // Declarations and consent are never answered for me, whatever I saved.
  const NEVER = /certify|declar|attest|acknowledg|consent|i agree|agree to|terms|privacy|policy|gdpr|data processing|criminal|convict|background check|drug test|referr(ed|al)|who referred/

  const STOP_WORDS = /otp|one[- ]time (pass|code)|verification code|captcha/

  function norm(text) {
    return String(text || "").toLowerCase().replace(/[*:?()\[\]_\-]+/g, " ").replace(/\s+/g, " ").trim()
  }

  /**
   * @param {{label?: string, name?: string, id?: string, placeholder?: string, autocomplete?: string,
   *          type?: string, tag?: string}} f
   */
  function classify(f) {
    const type = (f.type || "").toLowerCase()
    if (["hidden", "submit", "button", "reset", "image", "password", "search"].includes(type)) return { kind: "skip" }
    const text = norm([f.label, f.placeholder, f.name, f.id].filter(Boolean).join(" | "))
    const auto = (f.autocomplete || "").toLowerCase()
    if (auto === "one-time-code" || STOP_WORDS.test(text)) return { kind: "yours", reason: "verification code" }
    // Checkboxes and radios are almost always declarations or demographic choices.
    if (type === "checkbox" || type === "radio") {
      const hit = MY_CALL.find(([re]) => re.test(text))
      const answer = type === "radio" ? answerKey(text) : null  // radios: label = the group's question
      return { kind: "yours", reason: hit ? hit[1] : "a choice only you can make", ...(answer ? { answer } : {}) }
    }
    if (type === "file") {
      if (/cover/.test(text)) return { kind: "yours", reason: "cover letter" }
      if (/resume|\bcv\b|curriculum/.test(text)) return { kind: "fill", key: "resume_file" }
      return { kind: "skip" }
    }
    const answer = answerKey(text)
    const hit = MY_CALL.find(([re]) => re.test(text))
    if (answer) return { kind: "yours", reason: hit ? hit[1] : "saved answer", answer }
    if (hit) return { kind: "yours", reason: hit[1] }
    if (type === "email" || auto === "email") return { kind: "fill", key: "email" }
    if (type === "tel" || auto === "tel") return { kind: "fill", key: "phone" }
    if (auto === "given-name") return { kind: "fill", key: "first_name" }
    if (auto === "family-name") return { kind: "fill", key: "last_name" }
    if (auto === "name") return { kind: "fill", key: "full_name" }
    // Label wins over name/id: "Name" with name="urls[LinkedIn]" is a LinkedIn field, not a name field.
    for (const source of [norm(f.label), text]) {
      if (!source) continue
      const fact = FACTS.find(([, re]) => re.test(source))
      if (fact) return { kind: "fill", key: fact[0] }
    }
    return { kind: "skip" }
  }

  function answerKey(text) {
    if (!text || NEVER.test(text)) return null
    const a = ANSWERS.find(([, re]) => re.test(text))
    return a ? a[0] : null
  }

  const FOREIGN = /united states|\bu\.?s\.?a?\b|america|united kingdom|\bu\.?k\.?\b|britain|england|canada|europe|\beu\b|germany|france|netherlands|ireland|australia|new zealand|singapore|\buae\b|dubai|japan|outside india|any country other/
  const CITY_ALIASES = { bengaluru: /bengaluru|bangalore/, hyderabad: /hyderabad|secunderabad/, chennai: /chennai|madras/ }

  /** The city (from my relocation list) this job is in, read from the page text. */
  function jobCity(pageText, cities) {
    const t = norm(pageText)
    let best = null
    for (const city of cities || []) {
      const re = CITY_ALIASES[norm(city)] || new RegExp(`\\b${norm(city)}\\b`)
      const m = t.match(re)
      if (m && (best === null || m.index < best.index)) best = { city, index: m.index }
    }
    return best ? best.city : null
  }

  /**
   * My saved answer for this question, or null when it doesn't apply (then the field stays for me).
   * ctx: { question, inputType: "text" | "number" | "date" | "select" | "radio" | "textarea", city }
   */
  function resolveAnswer(key, answers, ctx) {
    if (!answers) return null
    const q = norm(ctx.question)
    const t = ctx.inputType
    switch (key) {
      case "work_auth": return FOREIGN.test(q) ? null : answers.work_auth_india || null
      case "sponsorship": return FOREIGN.test(q) ? null : answers.sponsorship_india || null
      case "relocate": return ctx.city ? "Yes" : null
      case "current_ctc": return t === "number" ? "0" : answers.current_ctc || null
      case "expected_ctc": return t === "number" ? null : answers.expected_ctc || null  // a bare number's unit is unclear
      case "notice_period": return t === "number" ? "0" : answers.notice_period || null
      case "start_date": return t === "date" ? null : answers.start_date || null
      case "dob": {
        if (!answers.dob) return null
        const [y, m, d] = answers.dob.split("-")
        return t === "date" ? answers.dob : `${d}/${m}/${y}`
      }
      case "tenth": case "twelfth": {
        const e = answers[key] || {}
        if (/\byear\b|passing|passed out/.test(q)) return e.year || null
        if (/board|school name|institution/.test(q)) return e.board || null
        return e.score || null
      }
      default: return answers[key] || null
    }
  }

  const YES = /^(yes|y|true)\b/
  const NO = /^(no|n|false)\b/

  /** Index of the option that means `value` ("Yes", "No", "Male", "Immediate"…), or -1. Never "female" for "male". */
  function matchChoice(options, value) {
    const v = norm(value)
    if (!v) return -1
    const opts = options.map(norm)
    if (v === "yes" || v === "no") {
      const re = v === "yes" ? YES : NO
      return opts.findIndex((o) => re.test(o))
    }
    let idx = opts.findIndex((o) => o === v)
    if (idx === -1) idx = opts.findIndex((o) => new RegExp(`^${v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(o))
    if (idx === -1 && v.length >= 4) idx = opts.findIndex((o) => new RegExp(`\\b${v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(o))
    return idx
  }

  /** Sites whose forms Scout must never touch (spec: no LinkedIn Easy Apply, no Wellfound). */
  function blockedSite(hostname) {
    return /(^|\.)linkedin\.com$|(^|\.)wellfound\.com$|(^|\.)angel\.co$/.test(String(hostname || "").toLowerCase())
  }

  /** Pick the <option> that matches a value ("B.Tech" -> "Bachelor of Technology" only if the text contains it). */
  function matchOption(options, value) {
    const v = norm(value)
    if (!v) return -1
    return matchChoice(options, value)
  }

  const api = { classify, blockedSite, matchOption, matchChoice, resolveAnswer, jobCity, answerKey, norm }
  if (typeof module !== "undefined" && module.exports) module.exports = api
  else root.ScoutFields = api
})(typeof globalThis !== "undefined" ? globalThis : this)
