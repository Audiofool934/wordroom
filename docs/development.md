# Development

## Environment

Use Python 3.12 and Node.js 22.
Run `bash scripts/cloud-setup.sh` to validate a fresh checkout; it runs every check.
The app needs no third-party Python packages and has no frontend build step.
Tests use temporary databases and a mock model service, so they need no private data, keys or paid API access.

For a local preview with a throwaway data directory:

```sh
python3 server.py --data-dir .qa/dev --port 8768
```

Without a built word list, `/` shows a short setup page.
To preview the real list, build it into the same directory with `scripts/build_wordlist.py`.
Stop the preview server after testing.

## Work and publish

1. Start from the latest `main` and create a feature branch.
2. Read `AGENTS.md`, make the change, and run `bash scripts/check.sh`.
3. Verify affected browser flows with test data.
4. Open a pull request and check the `Tests` result.

Every pull request and every push to `main` runs the checks.
Nothing is deployed automatically.

## Running an instance on your tailnet

Run a separate clone of `main`, so branch work in a development checkout does not change it.
Give it a data directory outside the clone and start it behind Tailscale Serve:

```sh
python3 server.py --port 8770 --data-dir "$HOME/wordroom-data" \
  --public-origin https://<machine>.<tailnet>.ts.net --tailscale-user <your-tailscale-login>
tailscale serve --bg 8770
```

The server listens on loopback only.
Serve provides HTTPS and the `Tailscale-User-Login` header; Wordroom accepts only the named user and checks the browser origin on writes.
Keep it running with your system's service manager, such as a launchd agent on macOS or a systemd unit on Linux.

To release a change, pull the clone, rebuild the page with `scripts/build_wordlist.py` if the builder or templates changed, and restart the service.
Back up first with `python3 store.py --data-dir <dir>` when a release changes the database.
