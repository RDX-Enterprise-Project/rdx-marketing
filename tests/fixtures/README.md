# Contract fixtures

Captured Buffer responses that `tests/test_contract_buffer.py` pins the adapter
to. The tests run **offline**, so the suite never touches the network, and these
files are what change when Buffer changes shape.

Each fixture carries a `_fixture` block with `provenance: synthetic | live`.
Contract tests that can only mean something against a real response are
**skipped** while a fixture is synthetic, and name what would settle them.

## The two unknowns these fixtures exist to close

1. **The `PostInputMetaData` key for a first comment.** Buffer documents that
   per-network extras live in `metadata` but not the exact field name. If the
   guess in `FIRST_COMMENT_METADATA_KEY` is wrong, `createPost` returns a
   `MutationError` and `test_the_live_draft_response_confirms_the_first_comment_metadata_key`
   fails with Buffer's own message in the assertion.

2. **The scalar name in the metrics query** (`PostId` vs `ID`). A wrong scalar
   is a GraphQL validation error, caught by
   `test_the_live_metrics_query_scalar_name_is_accepted`.

## Capturing

**The create capture writes to your Buffer account.** There is no way to get a
real `createPost` response without calling `createPost`. It is sent with
`saveToDraft: true`, so the post is staged and never published, but a draft does
appear and you should delete it. The script prints its id.

```bash
export BUFFER_ACCESS_TOKEN="..."
export BUFFER_CHANNEL_LINKEDIN="..."

python scripts/capture_fixtures.py                     # dry run, sends nothing
python scripts/capture_fixtures.py --execute           # creates ONE draft
git diff tests/fixtures/                               # read before committing
```

Metrics cannot come from a fresh draft — a post that has not been sent has no
metrics. Capture that fixture separately against a post that has actually gone
out:

```bash
python scripts/capture_fixtures.py --execute --metrics-post-id <sent post id>
```

## What is stripped

| Field | Why |
| --- | --- |
| `channelId` | Identifies the connected social account. |
| `id`, `postId`, `organizationId` | Account-scoped identifiers. |
| Anything matching `Bearer <token>` | A credential must never reach a committed file. |

`test_no_account_identifiers_survive_in_the_fixture` re-checks this on every
run, so an unsanitised capture fails the build rather than getting committed.
