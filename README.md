# JollyRating

A season-based rating system for a [vjudge](https://vjudge.net) group.
It downloads the standings of every contest in your group, turns each one into a
*performance* per participant, and combines the performances into a rating that
never decreases within a season. Output is a JSON/CSV/Markdown leaderboard and a
self-contained `index.html` with per-user rating history.

The rules are summarised in [How the rating works](#how-the-rating-works).

## Quick start

Requirements: Python 3.11+ and the `requests` package.

```bash
git clone https://github.com/VennethN/JollyRating
cd JollyRating
pip install -e .            # or: pip install requests
```

Try it on synthetic demo data (no network needed):

```bash
python examples/demo/make_fixtures.py          # writes examples/demo/data/
python -m jollyrating -c examples/demo/jollyrating.toml compute
open examples/demo/out/index.html              # or xdg-open / start
```

Then set up your own group:

1. **List your contests.** Edit `jollyrating.toml` and add one `[[contests]]`
   table per contest. The id is the number in the contest URL,
   `https://vjudge.net/contest/<id>`. Set `[vjudge] group = "<name>"` and run
   `python -m jollyrating discover --write` to have the group page scanned for
   contest ids instead (see [Discovering contests](#discovering-contests)).
2. **Define the seasons** with `[[seasons]]` (name + start date). Everything
   before the second season's start belongs to the first, and so on.
3. **Fetch and compute:**

   ```bash
   export VJUDGE_COOKIE='JSESSIONlD=...; Jax.Q=...'   # only for private contests, see below
   python -m jollyrating run
   ```

   `run` is `fetch` (download each contest's standings into
   `data/contests/<id>.json`, once) followed by `compute` (write `out/`).

4. Look at `out/index.html`, share `out/ratings.md`, or automate everything
   with the included [GitHub Actions workflow](#automating-with-github-actions).

### Commands

| Command | What it does |
|---|---|
| `validate` | Check the config and show which contests still lack data. |
| `discover [--group NAME] [--write]` | List the contests found on the group page; `--write` appends new ones to the config. |
| `fetch [--force] [--ids ...]` | Download standings for the configured vjudge contests. Cached contests are skipped unless they were still running when cached. |
| `compute [--out DIR] [--top N] [--ignore-missing]` | Compute ratings from the cached data and write the reports. |
| `run` | `fetch` then `compute`. |
| `explain USER [--season NAME]` | Print every number that went into one user's rating. |

All commands take `-c path/to/config.toml` (default `jollyrating.toml`) and `-v`
for debug logging.

## Private contests

Group contests are usually private, and vjudge only serves their standings to a
logged-in member. There is no API token; the tool sends the cookies of your
browser session instead:

1. Log in to vjudge in your browser and open any contest of the group.
2. Open the developer tools (F12) → *Network* tab, reload, click the first
   request to `vjudge.net`, and copy the whole value of the **Cookie** request
   header. The part that matters is `JSESSIONlD=<user id>|<token>` (spelled
   with a lowercase L); the other cookies can stay.
3. Put it in the `VJUDGE_COOKIE` environment variable, or in a git-ignored file
   named by `cookie_file` in the config, e.g. `cookie_file = ".cookie"`.

Cookies expire after a while; when `fetch` starts reporting login pages, copy a
fresh one. **Never commit the cookie** – it is your vjudge login.

**Cloudflare.** vjudge sits behind Cloudflare. If your cookie header contains
`cf_clearance`, Cloudflare only accepts it from the same IP address and with
the same browser `User-Agent` it was issued to: copy the request's
`User-Agent` header into `[vjudge] user_agent` in the config. From another
network (for example GitHub Actions) the challenge may block `fetch` with
"vjudge returned an HTML page instead of JSON"; then run `fetch` on your own
machine and commit the resulting `data/contests/*.json`, which the workflow's
`compute` step uses as-is.

**Manual fallback.** If fetching does not work for some contest, open
`https://vjudge.net/contest/rank/single/<id>` in the logged-in browser, save the
JSON as `data/contests/<id>.json`, and run `compute`. That file is exactly what
`fetch` would have stored.

## Discovering contests

vjudge has no documented API for listing a group's contests. `discover` scrapes
the group page for `/contest/<id>` links and, as a second attempt, asks the
contest list feed with a group filter (keeping the answer only if the filter
changed it). It prints what it found together with the source of each id; check
the list before `--write`, and add ids by hand if the page shows contests the
scraper missed.

## Configuration

`jollyrating.toml` is documented inline; the important parts:

```toml
title = "JollyBee Rating"
timezone = "Asia/Manila"        # dates below are interpreted here

[vjudge]
group = "jollybee"
cookie_env = "VJUDGE_COOKIE"

[rating]
penalty_minutes = 20            # ICPC penalty per rejected submission
include_zero_submission_participants = true

[[seasons]]
name = "Season 2024/25"
start = 2024-08-01

[[seasons]]
name = "Season 2025/26"
start = 2025-08-01

[[contests]]
id = 654321

[[contests]]
id = 654400
title = "Mock ICPC"             # optional overrides: title, season, penalty_minutes, begin, skip

[[contests]]
id = 654500
mode = "ioi"                    # OI-style vjudge contest: sum of per-problem scores

[[contests]]
id = "ioi-week-3"               # non-vjudge or IOI-style contest from a file
mode = "ioi"
file = "data/manual/ioi-week-3.json"

[[external]]
user = "alice"
contest = 654400
note = "ICPC Asia Regional"

[users]
exclude = ["testaccount"]
aliases = { alice_old = "alice" }
display_names = { alice = "Alice Tan" }
```

### Seasons

A contest belongs to the last season whose `start` is not after the contest's
start time (`season = "..."` on a contest overrides this). A season may also
have an `end`; a contest that falls in a gap is an error, so you notice. With no
`[[seasons]]` at all, everything is one season.

### External participations

If a user misses a contest because of another training or competition, add an
`[[external]]` entry with the user, the contest they missed and a note. The
i-th external participation of a season (in date order) counts as a performance
equal to the user's i(i+1)/2-th best actual performance of that season (the
1st copies the best, the 2nd the 3rd best, the 3rd the 6th best, ...), or 0 when
the user has fewer actual performances than that. Use `season = "..."` instead
of `contest` for an external participation that does not correspond to a
specific contest.

### IOI-style and off-platform contests

Contests are scored ICPC-style unless told otherwise (`[rating] default_mode`).
For a vjudge contest that was run OI-style, set `mode = "ioi"` on its
`[[contests]]` entry: the score is then the sum, over problems, of the best
score of any submission as reported by vjudge (partial scores count, there is
no penalty). `validate` points out cached contests that carry per-problem
scores. Contests held elsewhere come from a file:

```json
{
  "title": "IOI-style practice #1",
  "begin": "2025-11-08T09:00:00+08:00",
  "mode": "ioi",
  "standings": [
    {"user": "amara", "score": 287.5},
    {"user": "bayu", "score": 300, "display": "Bayu P."}
  ]
}
```

A CSV with columns `user,score[,penalty][,display]` works too; then set `title`
and `begin` on the `[[contests]]` entry. In IOI mode the penalty is always 0 and
ties share a rank. `mode = "icpc"` with a `penalty` column is accepted for
ICPC contests held outside vjudge.

### Users

Users are keyed by vjudge username. `aliases` merges accounts (old → new),
`exclude`/`only` control who is rated (excluded users are also removed from the
participant count), and `display_names` overrides the shown name (by default the
vjudge nickname, falling back to the username).

## Output

`compute` writes to `out/`:

* `index.html` – leaderboard, per-user breakdown (every performance, external
  participation and weight) and a rating-over-time chart. Works offline; drop it
  on any static host.
* `ratings.md` – Markdown leaderboard, e.g. for a README or Discord.
* `ratings.csv` – one row per user with the rating at the end of every season.
* `ratings.json` – everything, for your own tooling.

`explain <user>` prints the same breakdown in the terminal.

## Automating with GitHub Actions

`.github/workflows/update-ratings.yml` runs daily (and on demand): it
discovers new group contests (when `[vjudge] group` is set), fetches new
standings, recomputes, commits `jollyrating.toml`, `data/` and `out/` back to
the repository, and can publish `out/` to GitHub Pages.

1. Commit your filled-in `jollyrating.toml` (the workflow does nothing while
   there are no `[[contests]]`).
2. For private contests add a repository **secret** `VJUDGE_COOKIE`
   (Settings → Secrets and variables → Actions). Refresh it when it expires.
3. Optional: Settings → Pages → *Source: GitHub Actions*, then add a repository
   **variable** `DEPLOY_PAGES` = `true`. The leaderboard is then served at
   `https://<user>.github.io/<repo>/`.

Because raw standings are committed under `data/contests/`, ratings stay
reproducible and `compute` keeps working even when vjudge is unreachable.

### Running the update from your computer

vjudge sits behind a Cloudflare bot challenge, which GitHub's servers cannot
pass (the workflow log then says "blocked by Cloudflare's bot challenge"). A
machine where you are logged in to vjudge in a browser can, so the fetch runs
there and GitHub only publishes:

1. Clone the repository, `pip install -e .`, and put your browser's Cookie
   header in a file named `.cookie` (git-ignored).
2. In `jollyrating.toml` set `cookie_file = ".cookie"`, `user_agent` to your
   browser's User-Agent string, and `group` to the group name.
3. Run `./scripts/update.sh`. It discovers new contests, fetches their
   standings, recomputes, commits and pushes; the push triggers the workflow,
   which publishes the page.

To make it automatic, schedule that script. With cron (Linux/macOS):

```
0 22 * * * cd /path/to/JollyRating && ./scripts/update.sh >> update.log 2>&1
```

Alternatively keep everything inside GitHub Actions by running the workflow's
update job on your own machine: install a self-hosted runner (Settings →
Actions → Runners → New self-hosted runner, follow the commands shown), add
the repository variable `UPDATE_RUNNER` = `self-hosted`, and keep the
`VJUDGE_COOKIE` secret and the `user_agent` config as above. The scheduled
and manual runs then fetch from your network while GitHub still publishes.

The cookie expires every few weeks; when the log shows login errors, copy a
fresh one into `.cookie`. Contests that are still running are fetched again on
the next run, so a contest is final in the ratings one run after it ends.

## How the rating works

For a user *u* and contest *c*:

* `score(u, c)` – solved problems (ICPC) or raw score (IOI).
* `penalty(u, c)` – ICPC penalty: for every solved problem, the time of the
  accepted submission plus 20 minutes per rejected submission before it (0 for
  IOI). Submissions after the contest ended are ignored.
* `rank(u, c)` – 1 + the number of participants with a larger score, or the same
  score and a smaller penalty. Equal (score, penalty) share a rank.
* `U(c)` – the participants of the contest (everyone in vjudge's standings,
  including members who joined but did not submit, unless
  `include_zero_submission_participants = false`).

**Performance** (a real number in [0, 4000]):

```
perf(u, c) = 4000 × (|U(c)| − rank(u, c) + 240 + 1) / (|U(c)| + 240) × score(u, c) / max_v score(v, c)
```

The best participant always gets 4000; everyone else gets a fraction based on
rank and on their share of the top score. If nobody scored, every performance is
0.

**External participation.** With *P* the user's actual performances of the
season sorted in non-increasing order, the i-th external participation is worth
`P[i(i+1)/2]`, or 0 if `i(i+1)/2 > |P|`.

**Rating.** Let *P′* be the season's performances (actual + external), sorted
in non-increasing order, and

```
W_i = max(0.01, 0.1 × 0.95^(i−1))        # 0.01 from the 46th performance on
rating_s(u) = seasonReset(rating_{s−1}(u)) + Σ_i W_i × P′_i
rating_0(u) = 0
seasonReset(x) = x^c,  c = log_4000(400) ≈ 0.722381
```

A rating of 4000 at the end of a season becomes a bonus of 400 at the start of
the next one; the higher the previous rating, the harder it is to raise the
bonus further. Within a season the rating can only go up, so skipping a contest
is never better than participating.

## Development

```bash
python -m unittest discover -s tests -v
python examples/demo/make_fixtures.py && python -m jollyrating -c examples/demo/jollyrating.toml compute
```

The tests pin the formulas to hand-computed values and run the CLI end to end on
synthetic data.
