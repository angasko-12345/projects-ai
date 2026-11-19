# Repository ownership and mirror policy

## Canonical source

The `qr-generator/` directory in `angasko-12345/projects-ai` is the canonical
source for QR Generator code. Make implementation changes here first and review
them with this repository's typecheck, lint, and Jest workflow.

The standalone [`angasko-12345/qr-generator`](https://github.com/angasko-12345/qr-generator)
repository is a public distribution mirror, not a second development source.
Do not make independent implementation changes there. Before a public app
release, copy the reviewed source from this directory into the standalone repo
and verify its build there.

## Known reconciliation work

The copies are currently not synchronized. The standalone repository has
`docs/`, `marketing/`, and `PRIVACY_POLICY_CORRECTIONS.md` absent here; this
canonical copy has newer `src/util/feedback*.ts` and `review*.ts` code. Before
the next public release:

1. Review and bring the standalone documentation, marketing, and privacy-policy
   corrections into this canonical copy where they remain accurate.
2. Confirm the app source and release metadata in the standalone repository
   match this directory.
3. Run the standalone repository's build and test checks, then publish from
   that mirror.
4. Update this checklist and the root project catalog when the copies are
   reconciled.

This policy resolves where future code changes belong; it does not claim that
the external repository has already been updated. Its history is separate, so
reconciliation and mirror updates require an explicit release task.
