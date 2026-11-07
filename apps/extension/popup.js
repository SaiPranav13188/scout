"use strict"
const who = document.getElementById("who")
const btn = document.getElementById("fill")
const status = document.getElementById("status")

const BLOCKED = /(^|\.)linkedin\.com$|(^|\.)wellfound\.com$|(^|\.)angel\.co$/

async function init() {
  const { profile, resumeFile } = await chrome.storage.local.get(["profile", "resumeFile"])
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  const host = tab && tab.url ? new URL(tab.url).hostname : ""
  if (!profile) {
    who.innerHTML = 'No profile yet. <a href="options.html" target="_blank">Import it</a> from Scout first.'
    return
  }
  who.textContent = `${profile.full_name || "Your profile"}${resumeFile ? ` · resume: ${resumeFile.name}` : " · no resume file"}`
  if (!tab || !/^https?:/.test(tab.url || "")) { status.textContent = "Open a job application page first."; return }
  if (BLOCKED.test(host)) {
    status.className = "warn"
    status.textContent = "Scout never fills LinkedIn or Wellfound forms. Apply there yourself."
    return
  }
  btn.disabled = false
  btn.addEventListener("click", async () => {
    btn.disabled = true
    status.textContent = "Filling…"
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["src/fields.js", "src/content.js"] })
    } catch (e) {
      status.className = "warn"
      status.textContent = `Couldn't run on this page: ${e.message}`
    }
  })
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== "scout-autofill-result") return
  const r = msg.result
  if (r.status === "filled") status.textContent = `Filled ${r.filled} field(s). ${r.yours} need you. Review, then submit yourself.`
  else { status.className = "warn"; status.textContent = r.message }
})

init()
