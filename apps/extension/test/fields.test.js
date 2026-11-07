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
