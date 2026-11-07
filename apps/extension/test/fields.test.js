"use strict"
const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const { classify, blockedSite, matchOption } = require("../src/fields.js")

const fill = (label, extra = {}) => classify({ label, type: "text", ...extra })

test("plain facts are filled", () => {
  const cases = {
    "First Name *": "first_name", "Last Name": "last_name", "Full name": "full_name", "Email": "email",
    "Phone Number": "phone", "LinkedIn Profile": "linkedin", "GitHub URL": "github", "Portfolio / Website": "portfolio",
    "Name of your college": "school", "University": "school", "Degree": "degree", "Branch / Specialization": "discipline",
    "Graduation year": "graduation_year", "Expected graduation date": "graduation_year", "CGPA": "gpa",
    "Current City": "location", "Total years of experience": "years_experience",
  }
  for (const [label, key] of Object.entries(cases)) assert.deepEqual(fill(label), { kind: "fill", key }, label)
})

test("questions that are my call are never filled", () => {
  for (const label of [
    "Are you legally authorized to work in India?", "Will you now or in the future require visa sponsorship?",
    "Gender", "Expected CTC", "Current salary", "Notice period", "Are you willing to relocate?",
    "I certify that the information is true", "How did you hear about us?", "Why do you want to work here?",
    "Cover letter", "Current employer", "Earliest start date",
  ]) assert.equal(fill(label).kind, "yours", label)
  assert.equal(classify({ label: "I agree to the privacy policy", type: "checkbox" }).kind, "yours")
  assert.equal(classify({ label: "Python", type: "checkbox" }).kind, "yours")  // any checkbox is a choice for me
  assert.equal(classify({ label: "Cover Letter", type: "file" }).kind, "yours")
})

test("verification codes, passwords and buttons are left alone", () => {
  assert.equal(fill("Enter OTP").kind, "yours")
  assert.equal(classify({ label: "Code", type: "text", autocomplete: "one-time-code" }).kind, "yours")
  assert.equal(classify({ label: "Password", type: "password" }).kind, "skip")
  assert.equal(classify({ label: "Submit", type: "submit" }).kind, "skip")
})

test("input type and autocomplete hints", () => {
  assert.deepEqual(classify({ label: "", type: "email" }), { kind: "fill", key: "email" })
  assert.deepEqual(classify({ label: "", type: "tel" }), { kind: "fill", key: "phone" })
  assert.deepEqual(classify({ label: "", type: "text", autocomplete: "given-name" }), { kind: "fill", key: "first_name" })
  assert.deepEqual(classify({ label: "Resume/CV", type: "file" }), { kind: "fill", key: "resume_file" })
  assert.deepEqual(classify({ label: "LinkedIn", type: "text", name: "urls[LinkedIn]" }), { kind: "fill", key: "linkedin" })
  assert.equal(fill("Anything else you'd like to share?").kind, "yours")
  assert.equal(fill("Favourite colour").kind, "skip")
})

test("LinkedIn and Wellfound are blocked", () => {
  for (const h of ["www.linkedin.com", "linkedin.com", "in.linkedin.com", "wellfound.com", "angel.co"]) assert.ok(blockedSite(h), h)
  for (const h of ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com", "notlinkedin.com.evil.io"]) assert.ok(!blockedSite(h), h)
})

test("select options", () => {
  assert.equal(matchOption(["Select…", "B.Tech", "M.Tech"], "B.Tech"), 1)
  assert.equal(matchOption(["Select…", "Bennett University", "Other"], "Bennett University"), 1)
  assert.equal(matchOption(["Select…", "Yes", "No"], "B.Tech"), -1)
})

test("the content script can never submit or click", () => {
  const src = fs.readFileSync(path.join(__dirname, "../src/content.js"), "utf8")
  // Only the panel's own Undo / Close buttons get click *listeners*; nothing is ever clicked or submitted.
  assert.doesNotMatch(src, /\.click\(\)|\.submit\(|requestSubmit|dispatchEvent\(new (Mouse|Pointer)Event|KeyboardEvent/)
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../manifest.json"), "utf8"))
  assert.deepEqual(manifest.permissions.sort(), ["activeTab", "scripting", "storage"])
  assert.equal(manifest.host_permissions, undefined)  // no standing access to any site
  assert.equal(manifest.content_scripts, undefined)   // runs only when I click "Fill this page"
})

const { resolveAnswer, matchChoice, jobCity } = require("../src/fields.js")
const ANSWERS = {
  relocate_cities: ["Bengaluru", "Hyderabad", "Chennai"], notice_period: "Immediate", start_date: "Immediately",
  current_ctc: "0 (Fresher)", expected_ctc: "5-6 LPA (negotiable)", work_auth_india: "Yes", sponsorship_india: "No",
  backlogs: "No", tenth: { board: "SSC", score: "10 CGPA", year: null }, twelfth: { board: "Intermediate", score: "96.8%", year: null },
  dob: "2005-07-08", gender: "Male", disability: "No", heard_from: "LinkedIn", languages: "English, Hindi, Telugu",
}
const ans = (label, inputType = "text", city = null) => {
  const v = classify({ label, type: inputType === "radio" ? "radio" : "text" })
  return v.answer ? resolveAnswer(v.answer, ANSWERS, { question: label, inputType, city }) : undefined
}

test("saved answers fill the questions I answered once", () => {
  assert.equal(ans("Are you legally authorized to work in India?"), "Yes")
  assert.equal(ans("Will you require visa sponsorship?"), "No")
  assert.equal(ans("Notice period"), "Immediate")
  assert.equal(ans("Notice period (days)", "number"), "0")
  assert.equal(ans("Current CTC"), "0 (Fresher)")
  assert.equal(ans("Expected CTC"), "5-6 LPA (negotiable)")
  assert.equal(ans("Expected CTC (in INR)", "number"), null)  // unit unclear: left for me
  assert.equal(ans("Do you have any active backlogs?", "radio"), "No")
  assert.equal(ans("Date of birth", "date"), "2005-07-08")
  assert.equal(ans("Date of Birth (DD/MM/YYYY)"), "08/07/2005")
  assert.equal(ans("Gender", "radio"), "Male")
  assert.equal(ans("How did you hear about us?"), "LinkedIn")
  assert.equal(ans("12th percentage"), "96.8%")
  assert.equal(ans("10th board"), "SSC")
  assert.equal(ans("Year of passing 12th"), null)  // not saved: left for me
})

test("answers that depend on the job stay safe", () => {
  assert.equal(ans("Are you legally authorized to work in the United States?"), null)
  assert.equal(ans("Will you require sponsorship for a UK work visa?"), null)
  assert.equal(ans("Are you willing to relocate?", "radio", "Hyderabad"), "Yes")
  assert.equal(ans("Are you willing to relocate?", "radio", null), null)
  assert.equal(jobCity("Software Engineer · Bangalore, Karnataka", ANSWERS.relocate_cities), "Bengaluru")
  assert.equal(jobCity("Remote, India", ANSWERS.relocate_cities), null)
  assert.equal(classify({ label: "I certify that my answers are true", type: "checkbox" }).answer, undefined)
  assert.equal(classify({ label: "Were you referred by an employee?", type: "radio" }).answer, undefined)
})

test("choices are matched exactly", () => {
  assert.equal(matchChoice(["Select…", "Female", "Male", "Prefer not to say"], "Male"), 2)
  assert.equal(matchChoice(["None", "No", "Yes"], "No"), 1)
  assert.equal(matchChoice(["Yes, I am authorized", "No"], "Yes"), 0)
  assert.equal(matchChoice(["15 days", "Immediate joiner", "30 days"], "Immediate"), 1)
})
