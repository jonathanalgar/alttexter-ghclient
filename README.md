# alttexter-ghclient

[![Screenshot of alttexter-ghclient having made commit and raised review comment](example.png "alttexter-ghclient commit and review")](https://github.com/jonathanalgar/langchain/pull/3/files#diff-13f93b90ba1f5e943cfbd17a52a1ab71a43c3dd7797e96f959cc17dccaaac198)

![ci](https://github.com/jonathanalgar/alttexter-ghclient/actions/workflows/build-docker.yml/badge.svg)

![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://makeapullrequest.com)

## Overview

Containerized GitHub action for interacting with the [alttexter](https://github.com/jonathanalgar/alttexter) service.

The script searches for missing alt text and title attributes in markdown-formatted files on commit to a pull request. On match, it bundles the text and images off in a request to the [alttexter](https://github.com/jonathanalgar/alttexter) service for generation. Responses from the service are committed and a comment on the file posted.

### In the wild

* https://github.com/langchain-ai/langchain/pull/15357
* https://github.com/openai/openai-cookbook/pull/967

## Usage

First, create a new GitHub action workflow in your repo (eg. `.github/workflows/alttexter.yml`):

```yaml
name: alttexter

on:
  pull_request:
    types: [opened, synchronize]
    paths:
      - '**.md'
      - '**.mdx'
      - '**.ipynb'
      
permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  alttexter-ghclient:
    runs-on: ubuntu-latest
    container: 
      image: ghcr.io/jonathanalgar/alttexter-ghclient:v0.1
      credentials:
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}

    steps:
      - name: Set ref for checkout
        id: set_ref
        run: |
          echo "REF=${{ github.event.pull_request.head.ref }}" >> $GITHUB_ENV

      - name: Checkout
        uses: actions/checkout@v4.1.1
        with:
          fetch-depth: 1
          ref: ${{ env.REF }}

      - name: Run script
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          ALTTEXTER_ENDPOINT: ${{ secrets.ALTTEXTER_ENDPOINT }}
          ALTTEXTER_TOKEN: ${{ secrets.ALTTEXTER_TOKEN }}
          ALTTEXTER_RATEMINUTE: ${{ secrets.ALTTEXTER_RATEMINUTE }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python /app/alttexter-ghclient.py
```

You'll need to set the following repo secrets:

* `ALTTEXTER_ENDPOINT`: Endpoint URL of the running `alttexter` (eg. `https://alttexter-prod.westeurope.cloudapp.azure.com:9100/alttexter`)
* `ALTTEXTER_TOKEN`: Single token for service.
* `ALTTEXTER_RATEMINUTE`: Maximum number of calls to `alttexter` service per minute. Consider [OpenAI rate limits](https://platform.openai.com/docs/guides/rate-limits) for your tier. I'd reccomend no more than 3.

Once that's done you can open a PR with files that need alt text and/or title attributes.

## Features

* Helpful in running in docs repos on an ongoing basis or for one-off batch updates (fully async handy for this).
* Friendly UX that posts a review request to the PR after each commit.
* Handles both local image files and those defined as URL.
* Handles `md*` and `ipynb` files according to their respective formatting requirements.

## TODO

- [ ] Better error handling
- [ ] Create Issues for limitations identified in [LangChain repo batch update](https://github.com/langchain-ai/langchain/pull/15357#issue-2061030736).
- [ ] Extend this TODO list
