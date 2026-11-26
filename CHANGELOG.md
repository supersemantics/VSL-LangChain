# Changelog

What changed in each release, and why. Newest first.

## 0.2.0 (2026-11-26, tag `v0.2.0`): vsl-core 0.4, no F2 claims

**Before:** vsl-langchain required vsl-core 0.3, and every gate in the README and tests passed an
`AssuranceBasis` declaring F1/F2, which nothing checked. The README described this as self-declared.

**After:** vsl-core is `super-semantics-vsl>=0.4,<0.5`. Gates are built from a name, description and
monitor or rule; the README and tests pass no `assurance_basis`. The README's F1/F2 section is now
"What a gate reports", and the ledger section shows `write_action()` and `enforcement()`, which
compute from the ledger whether the gate held before the tool ran. Code that still passes
`assurance_basis` keeps working, with a `FutureWarning` from vsl-core.

**Why:** vsl-core 0.4.0 no longer takes a declared F2 level; enforcement is computed, not claimed.

## 0.1.2 (2026-11-13, tag `v0.1.2`): published on PyPI

The first version on PyPI: `pip install vsl-langchain`, which installs vsl-core (`super-semantics-vsl`)
with it. The code is unchanged from 0.1.1.

**Before:** vsl-core came from a GitHub URL pinned to `v0.3.0`, which PyPI does not accept in a
published package, and an application that also installed vsl-core had to name the same URL.

**After:** vsl-core is a normal dependency, `super-semantics-vsl>=0.3,<0.4`, resolved from PyPI; the
README gives `pip install vsl-langchain`, its links point to GitHub, and the package lists its repository.
A pushed release tag publishes from GitHub Actions through PyPI Trusted Publishing
(`.github/workflows/publish.yml`), after the tests pass.

**Why:** installs from a registry, with versions an application can share with its own dependencies.

## 0.1.1 (2026-11-12, tag `v0.1.1`): vsl-core pinned to v0.3.0

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
