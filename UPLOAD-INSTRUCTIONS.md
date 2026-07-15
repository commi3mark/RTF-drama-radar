# Automated X List Collector

This adds automated collection for the X List:

- List URL: https://x.com/i/lists/1040297289561063424
- List ID: `1040297289561063424`

## Files to upload

Place these paths in the root of the `RTF-drama-radar` repository:

```text
collect_x_list.py
.github/workflows/collect-x-list.yml
```

## Required GitHub secret

In the repository, open:

```text
Settings → Secrets and variables → Actions → New repository secret
```

Create:

```text
Name: X_BEARER_TOKEN
Value: your X API bearer token
```

Do not paste the token into the Python script or workflow file.

## How it runs

The GitHub Action:

- runs every three hours at minute 17;
- can also be launched manually from the Actions tab;
- reads new posts from the configured X List;
- deduplicates posts using the post ID;
- commits only when data changed.

## Output

```text
twitter/lists/1040297289561063424/latest.json
twitter/lists/1040297289561063424/state.json
twitter/lists/1040297289561063424/YYYY/MM/YYYY-MM-DD.json
```

The daily files contain:

- post text;
- author;
- timestamp;
- direct X URL;
- original/reply/quote/repost classification;
- conversation ID;
- referenced posts when returned by X;
- mentions and links;
- media metadata;
- available engagement figures.

## First test

After uploading the files and adding the secret:

1. Open the repository's **Actions** tab.
2. Select **Collect X List**.
3. Press **Run workflow**.
4. Check the run log.
5. Confirm that a new `twitter/lists/1040297289561063424/` folder was committed.

## Important

Access to X List posts depends on the access level and billing attached to your X developer account. If the workflow returns an X API permission or usage error, the GitHub setup is working but the X developer project lacks the required endpoint access.
