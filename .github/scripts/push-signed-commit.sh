#!/usr/bin/env bash
set -euo pipefail

# Push one commit that GitHub signs. A commit made with `git commit` and pushed
# with a token is unsigned, and the repositories these workflows push to require
# signed commits. GitHub signs the commits the GraphQL `createCommitOnBranch`
# mutation creates, user tokens such as RELEASE_TOKEN included: the token owner
# is the author and GitHub the committer.
#
# Adapted from matter-labs/zksync-os-zisk's copy, which pushes to its own
# repository (GITHUB_REPOSITORY); these workflows push to another one, and
# Actions does not let a step override GITHUB_*, hence TARGET_REPOSITORY.
#
# The mutation only appends to an existing branch, so the commit is built on a
# temporary branch at BASE and TARGET then moves to it. An existing TARGET is
# replaced in one ref update, so it never points at BASE in between.
#
# Usage: push-signed-commit.sh TARGET BASE MESSAGE_FILE EXPECTED_OLD PATH...
#   TARGET        branch to create or move to the new commit
#   BASE          parent of the new commit
#   MESSAGE_FILE  commit message: the first line is the headline, the rest the
#                 body
#   EXPECTED_OLD  TARGET's current commit, or "" when TARGET must not exist yet
#   PATH...       repository paths, read from the working tree; a path that does
#                 not exist there is deleted
#
# Required environment: GH_TOKEN, TARGET_REPOSITORY (owner/name). Prints the
# new commit.

: "${GH_TOKEN:?}" "${TARGET_REPOSITORY:?}"
if (( $# < 5 )); then
    echo "usage: $0 TARGET BASE MESSAGE_FILE EXPECTED_OLD PATH..." >&2
    exit 2
fi
target="$1"
base="$2"
message_file="$3"
expected_old="$4"
shift 4

work="$(mktemp -d)"
temp_branch="automation/signed-commit-$(date +%s)-$$"
cleanup() {
    gh api --method DELETE "repos/${TARGET_REPOSITORY}/git/refs/heads/${temp_branch}" \
        >/dev/null 2>&1 || true
    rm -rf "${work}"
}
trap cleanup EXIT

current_sha() {
    gh api "repos/${TARGET_REPOSITORY}/git/matching-refs/heads/$1" \
        --jq ".[] | select(.ref == \"refs/heads/$1\") | .object.sha"
}

echo '[]' > "${work}/additions.json"
echo '[]' > "${work}/deletions.json"
for path in "$@"; do
    if [[ -z "${path}" ]]; then
        echo "ERROR: empty path" >&2
        exit 2
    fi
    if [[ -f "${path}" ]]; then
        base64 < "${path}" | tr -d '\n' > "${work}/contents"
        jq --arg path "${path}" --rawfile contents "${work}/contents" \
            '. + [{path: $path, contents: $contents}]' \
            "${work}/additions.json" > "${work}/next.json"
        mv "${work}/next.json" "${work}/additions.json"
    else
        jq --arg path "${path}" '. + [{path: $path}]' \
            "${work}/deletions.json" > "${work}/next.json"
        mv "${work}/next.json" "${work}/deletions.json"
    fi
done

headline="$(head -n 1 "${message_file}")"
# The body without the blank lines that separate it from the headline.
body="$(tail -n +2 "${message_file}" | sed '/./,$!d')"

gh api --method POST "repos/${TARGET_REPOSITORY}/git/refs" \
    -f ref="refs/heads/${temp_branch}" -f sha="${base}" >/dev/null

jq -n \
    --arg repo "${TARGET_REPOSITORY}" \
    --arg branch "${temp_branch}" \
    --arg base "${base}" \
    --arg headline "${headline}" \
    --arg body "${body}" \
    --slurpfile additions "${work}/additions.json" \
    --slurpfile deletions "${work}/deletions.json" \
    '{
      query: "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
      variables: {input: {
        branch: {repositoryNameWithOwner: $repo, branchName: $branch},
        message: {headline: $headline, body: $body},
        expectedHeadOid: $base,
        fileChanges: {additions: $additions[0], deletions: $deletions[0]}
      }}
    }' > "${work}/request.json"
commit="$(gh api graphql --input "${work}/request.json" \
    --jq '.data.createCommitOnBranch.commit.oid')"

verified="$(gh api "repos/${TARGET_REPOSITORY}/commits/${commit}" \
    --jq '.commit.verification.verified')"
if [[ "${verified}" != true ]]; then
    echo "ERROR: GitHub did not sign ${commit}" >&2
    exit 1
fi

# The check and the ref update are two API calls, so a push to TARGET between
# them is overwritten; the callers own TARGET.
current="$(current_sha "${target}")"
if [[ "${current}" != "${expected_old}" ]]; then
    echo "ERROR: ${target} is at '${current}', expected '${expected_old}'" >&2
    exit 1
fi
if [[ -z "${current}" ]]; then
    gh api --method POST "repos/${TARGET_REPOSITORY}/git/refs" \
        -f ref="refs/heads/${target}" -f sha="${commit}" >/dev/null
else
    gh api --method PATCH "repos/${TARGET_REPOSITORY}/git/refs/heads/${target}" \
        -f sha="${commit}" -F force=true >/dev/null
fi
echo "${commit}"
