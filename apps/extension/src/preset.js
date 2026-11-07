/*
 * First run: if this copy of the extension was built with your profile and resume already inside
 * (`cli extension-build`), load them into storage so nothing has to be imported by hand.
 * The public copy in the repo has no preset folder, so this does nothing there.
 */
async function scoutLoadPreset() {
  const have = await chrome.storage.local.get(["profile", "resumeFile"])
  try {
    const res = await fetch(chrome.runtime.getURL("preset/profile.json"))
    if (!res.ok) return false
    const preset = await res.json()
    // Load on first run, and again whenever a newer build (`cli extension-build`) is installed.
    if (have.profile && (!preset.built_at || have.profile.built_at === preset.built_at)) return false
    const data = { profile: preset }
    const pdf = await fetch(chrome.runtime.getURL("preset/resume.pdf"))
    if (pdf.ok) {
      const bytes = new Uint8Array(await pdf.arrayBuffer())
      let bin = ""
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
      data.resumeFile = { name: data.profile.resume_file_name || "Resume.pdf", base64: btoa(bin) }
    }
    await chrome.storage.local.set(data)
    return true
  } catch (_) {
    return false
  }
}
