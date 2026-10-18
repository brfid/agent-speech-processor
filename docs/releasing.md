# Release procedure

Validate the exact tree and commit selected for release. Keep private audit evidence,
recordings, transcripts, model weights, local configuration and processing receipts
outside the release. The code is MIT licensed; tools/models keep their own licenses.
Publishing a repository, changing visibility and publishing a package are separate
actions with their own authorization.

## 1. Select and inspect the candidate

Record the candidate commit, intended public refs and working-tree state. Inspect
tracked filenames, source, documentation, examples, workflow files, package metadata
and commit metadata. If publishing a clean history, inspect the new public root and
only the history/refs intended to accompany it; preserve private development history
separately. Do not transfer private checkpoint refs or audit records.

Confirm one distribution/command (`agent-speech-processor`), the import namespace
(`agent_speech_processor`), no former-name entry points or config fallbacks, and
no changes to recognition settings disguised as documentation cleanup. Check the
[current formats and resume rules](workflow.md#current-formats-and-resume).

Check for real source/collection names, personal filesystem paths, device anecdotes,
credentials, recordings, weights, transcripts and processing/private records. Mark
fictional examples as invented. Generic product configuration paths and necessary
OS conventions are distinct from personal paths. Review licenses and attribution.
Secret scanning alone is not a content/privacy review.

## 2. Run source and content checks

Use Python 3.11 or newer and the [audio dependencies](quickstart.md#1-install-the-command):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
git diff --check
.venv/bin/python -m pip install build
.venv/bin/python -m build
gitleaks git --redact --no-banner --log-opts=--all
AGENT_SPEECH_PROCESSOR_RELEASE=$(mktemp -d)
git archive HEAD | tar -x -C "$AGENT_SPEECH_PROCESSOR_RELEASE"
gitleaks dir --redact --no-banner "$AGENT_SPEECH_PROCESSOR_RELEASE"
```

Inspect the archive and distribution file lists, not only the checkout. Check
relative Markdown links and guide ownership; stale implementation narratives must
not override current procedures. Confirm optional review imports/dependencies remain
outside supported commands and the base install. Tests use disposable synthetic
inputs; recordings and weights stay out of CI.

## 3. Check clean installations

Create a separate virtual environment outside the source checkout and install the
built wheel without dependencies. Run from outside the checkout so editable paths
cannot hide missing package files. Check `--version`, `--help`, supported commands
and their synthetic pipeline: draft/verify, promotion, role application/readiness,
reading and passage extraction. Use synthetic recognizer fixtures or an explicitly
installed model; do not download one implicitly for a release check.

Confirm model-free completed resume preserves the protected baseline and a new
editorial note. Check current-preface promotion, role completion and rejection of
unsupported formats. Inspect wheel modules against the source
package. Check a source-distribution installation too, including an installation
path containing spaces. Record check coverage and any omitted platform/runtime
checks; a packaging smoke test is not a speech-accuracy benchmark.

## 4. Verify the release commit and publish

Commit the reviewed candidate using authorized public attribution. Confirm that
the hosted macOS/Linux regression and Linux package jobs pass for that exact SHA;
consult the repository's [Actions](https://github.com/brfid/agent-speech-processor/actions).
Do not carry an older commit's result forward. Record the SHA, builds/checks, tool
versions, coverage, gaps and publication authorization in the appropriate release
record; keep private audit details outside the public tree. Update [STATUS](../STATUS.md)
with verified capability/results and the remaining next action.

After authorization, publish only the selected refs or change the intended
repository's visibility. Verify the remote commit and contents, links and CI after
publication. Package-registry publication, if requested, uses the same checked
artifacts and its own release/version decision. Revalidate any later changes.
