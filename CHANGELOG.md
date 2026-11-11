# Changelog

What changed in each release, and why. Newest first.

## 0.1.1 (unreleased): vsl-core pinned to v0.3.0

**Before:** vsl-core was installed from `git+https://github.com/supersemantics/VSL-Core.git`
with no version, so an install took whatever vsl-core's `main` was that day. An application could
not pin vsl-core itself either: pip refuses two different URLs for one package, so pinning
`@v0.3.0` next to this adapter failed to install.

**After:** vsl-core is pinned to its release tag, `...VSL-Core.git@v0.3.0`. An application that
also installs vsl-core names the same URL, tag included.

**Why:** reproducible installs; tested against vsl-core 0.3.0.

**Also:** the docs no longer point to a separate hosted ledger package; they name the ledger stores this library's core ships with.

## 0.1.0

The first release.
