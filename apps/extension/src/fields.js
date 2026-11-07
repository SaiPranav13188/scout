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
      return { kind: "yours", reason: hit ? hit[1] : "a choice only you can make" }
    }
    if (type === "file") {
      if (/cover/.test(text)) return { kind: "yours", reason: "cover letter" }
      if (/resume|\bcv\b|curriculum/.test(text)) return { kind: "fill", key: "resume_file" }
      return { kind: "skip" }
    }
    const hit = MY_CALL.find(([re]) => re.test(text))
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

  /** Sites whose forms Scout must never touch (spec: no LinkedIn Easy Apply, no Wellfound). */
  function blockedSite(hostname) {
    return /(^|\.)linkedin\.com$|(^|\.)wellfound\.com$|(^|\.)angel\.co$/.test(String(hostname || "").toLowerCase())
  }

  /** Pick the <option> that matches a value ("B.Tech" -> "Bachelor of Technology" only if the text contains it). */
  function matchOption(options, value) {
    const v = norm(value)
    if (!v) return -1
    let idx = options.findIndex((o) => norm(o) === v)
    if (idx === -1) idx = options.findIndex((o) => norm(o).startsWith(v) || (v.length >= 4 && norm(o).includes(v)))
    return idx
  }

  const api = { classify, blockedSite, matchOption, norm }
  if (typeof module !== "undefined" && module.exports) module.exports = api
  else root.ScoutFields = api
})(typeof globalThis !== "undefined" ? globalThis : this)
