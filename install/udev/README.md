# udev rules (kit-carried, M11a)

Rules referenced by a recipe's `postinst: [{udev: <path>}]` step live here and
ride the kit: the builder copies `install/udev/` to `kit/install/udev/`, and
the bootstrap executor (`gd_kit_postinst`) installs each declared rule to
`/etc/udev/rules.d/` (mode 0644; `udevadm control --reload` best-effort).

Contract (resolver-enforced, `tests/test_registry.py`):
- The step's path is installer-relative and must match `udev/<name>.rules` —
  one segment, safe charset, no traversal; the file must exist under
  `install/udev/` in the repo. The builder renders the kit-relative path
  (`install/udev/<name>.rules`) into `kit/postinst.tsv`; the executor joins
  it against the kit root and installs it as `/etc/udev/rules.d/<name>`
  (mode 0644; `udevadm control --reload` best-effort).
- The rule file must exist in the repo at recipe-flip time: the resolver
  refuses a declared `udev:` step whose file is missing.

Content discipline (same as recipe `config:` keys, M7): the MECHANISM ships
with M11a; each app's actual rule content (VID:PID pins for meshtasticd
handsets, MODE/GROUP for serial devices) is pinned from upstream sources at
that app's live E2E — Doctor's mesh-handset VID:PID sweep (cp210x/ch341) is
the acceptance reference. Until then no rule ships, so the vocabulary is
exercised by tests, not by kits.
