# Gate 6C publication runbook

1. Merge the reviewed preparation PR and freeze its exact commit.
2. Run the single authorized final-head package and full lifecycle validation; stop on failure.
3. Generate the seven-asset manifest/checksum set and rehash it independently.
4. Create the exact annotated tag only after acceptance; tag pushes do not trigger a workflow.
5. Manually create a draft release with prerelease state matching the semantic-version
   tag and upload exactly the manifest, SHA256/SHA512 sums, third-party notice, wrapper,
   payload, and `latest.yml`.
6. Record the draft's immutable numeric REST release ID, then dispatch the verifier from
   `trunk` with that release ID, exact tag, release commit, manifest hash,
   `allow_unsigned=true`, `accepted_name_risk=true`, and prerelease intent matching the
   tag. The immutable dispatch
   `github.sha` supplies the reviewed control-plane scripts; the release tag, draft
   `targetCommitish`, and manifest must independently identify the accepted release
   commit. The workflow fetches the draft by release ID and downloads each of the exact
   seven assets through its authenticated asset API URL; it never builds or uploads.
   GitHub requires `contents: write` on this otherwise read/download/verify-only job to
   access an existing draft release; checkout credentials remain disabled.
7. A configured `production-release` environment reviewer approves the promotion job.
   The job re-downloads and re-verifies, then publishes a hyphenated semantic-version
   tag with `prerelease=true` and `latest=false`; a stable tag is published with
   `prerelease=false` and marked latest.
8. Download public assets and compare hashes. On any mismatch stop: never move the tag or
   replace assets. Preserve evidence and follow the incident withdrawal procedure.
