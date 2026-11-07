"use strict"
const FIELDS = [
  ["first_name", "First name"], ["last_name", "Last name"], ["full_name", "Full name"], ["email", "Email"],
  ["phone", "Phone"], ["location", "City you live in"], ["linkedin", "LinkedIn URL"], ["github", "GitHub URL"],
  ["portfolio", "Portfolio / website"], ["school", "College / university"], ["degree", "Degree"],
  ["discipline", "Branch / major"], ["graduation_year", "Graduation year"], ["gpa", "CGPA / grade"],
  ["years_experience", "Years of full-time experience"],
]
const form = document.getElementById("form")
const msg = document.getElementById("msg")
let resumeFile = null

function render(profile = {}) {
  form.innerHTML = ""
  for (const [key, label] of FIELDS) {
    const l = document.createElement("label")
    l.textContent = label
    const i = document.createElement("input")
    i.name = key
    i.value = profile[key] ?? ""
    l.append(i)
    form.append(l)
  }
}

function read() {
  const out = { version: 1 }
  for (const [key] of FIELDS) {
    const v = form.elements[key].value.trim()
    out[key] = v || null
  }
  return out
}

document.getElementById("import").addEventListener("change", async (e) => {
  const f = e.target.files[0]
  if (!f) return
  try {
    const p = JSON.parse(await f.text())
    if (p.version !== 1) throw new Error("not a Scout profile file")
    render(p)
    msg.textContent = p.reviewed ? "Imported. Check the fields, then Save." : "Imported. Your resume isn't marked reviewed in Scout yet: check every field."
  } catch (err) {
    msg.style.color = "#b91c1c"
    msg.textContent = `Couldn't read that file: ${err.message}`
  }
})

document.getElementById("resume").addEventListener("change", async (e) => {
  const f = e.target.files[0]
  if (!f) return
  if (f.size > 5 * 1024 * 1024) { msg.textContent = "That PDF is over 5 MB."; return }
  const bytes = new Uint8Array(await f.arrayBuffer())
  let bin = ""
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  resumeFile = { name: f.name, base64: btoa(bin) }
  document.getElementById("resumeName").textContent = `Selected: ${f.name}`
})

document.getElementById("save").addEventListener("click", async () => {
  const data = { profile: read() }
  if (resumeFile) data.resumeFile = resumeFile
  await chrome.storage.local.set(data)
  msg.style.color = "#15803d"
  msg.textContent = "Saved."
})

scoutLoadPreset().then(() => chrome.storage.local.get(["profile", "resumeFile"])).then(({ profile, resumeFile: rf }) => {
  render(profile)
  if (rf) document.getElementById("resumeName").textContent = `Saved: ${rf.name}`
})
