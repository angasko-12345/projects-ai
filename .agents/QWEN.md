# Qwen Code Instructions

 Never work directly on main.

 Before making changes:

 - Run git status
- Run git branch --show-current
- Run git fetch origin
- Inspect the relevant code, tests, configuration, and documentation

 If on main:

 - git checkout main
- git pull --ff-only origin main
- Create a new branch

 Branch prefixes:

 - feature/
- fix/
- refactor/
- test/
- docs/
- chore/
- perf/
- security/

 Make the smallest change that solves the task. Do not modify unrelated code or rewrite working code without a reason.

 After changes, run the relevant tests, linting, type checks, and build commands defined by the project.

 If tests fail:

 - Determine whether the change caused the failure
- Fix the underlying issue
- Run the tests again
- Do not delete or weaken tests to make them pass
- Report pre-existing failures

 Never commit:

 - API keys
- Passwords
- Tokens
- Private keys
- Credentials
- Secrets
- .env files containing secrets

 Review git status and git diff before committing. Only stage files relevant to the task.

 Never:

 - Push directly to main
- Force-push main
- Delete main
- Rewrite main history
- Bypass branch protection
- Modify GitHub rulesets
- Disable required checks
- Approve or merge your own PR

 Use descriptive commit messages:

 feat: ...\
 fix: ...\
 test: ...\
 refactor: ...\
 docs: ...\
 chore: ...

 When the task is complete:

 1. Make sure you are on a feature/fix branch.
2. Review the full diff.
3. Run all relevant checks.
4. Commit the changes.
5. Push the branch.
6. Create or update a PR targeting main.

 If gh is available, use:

 gh pr create --base main --head \<branch-name\>

 PRs must include:

 - Summary
- Reason for the change
- Tests/checks performed
- Results
- Known issues

 When addressing PR feedback, use the existing PR branch and update the same PR.

 Ask before:

 - Deleting major functionality
- Deleting data
- Changing production infrastructure
- Changing database schemas
- Changing authentication architecture
- Changing deployment configuration
- Changing security controls

 Final response:

 - What changed
- Files changed
- Tests/checks run
- Results
- Branch
- Commit
- PR URL
- Remaining issues
