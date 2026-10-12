# Paid models vs the free baseline

Run on 2026-10-12, same code, same synthetic web (seed 7, 47 fictional sites, v1 and v2 layouts), same scoring as the
free run in [`results/`](..). Only the model changed. Paid models were reached through a local OpenAI-compatible
budget proxy with the new explicit opt-in (`SIFT_LLM_PROVIDER=compat`, `SIFT_LLM_BASE_URL`, `SIFT_LLM_API_KEY`,
`SIFT_ALLOW_PAID_MODELS=true`, `SIFT_LLM_SEND_TEMPERATURE=false` because the Claude 5.5 models reject `temperature`).
The default config still refuses every non-`:free` model.

| Role | Model | What it ran |
|---|---|---|
| Baseline (free) | `nvidia/nemotron-3-super-120b-a12b:free` (+ `nemotron-3-ultra`, `dots-3-note-preview` on the 8-site comparison) | committed `results/main_*.json`, `results/model_*_synthetic.json` |
| Main paid model | `claude-haiku-5-5` | every suite the baseline ran: synthetic (47 sites, v1 + v2 healing), direct (49 pages), schema (47), traps, toscrape (snapshots) |
| Strong model, hard subsets | `claude-opus-5-5` | synthetic suite on the README's 8-site comparison subset, and on the 7 "hard" sites where the free model failed to heal or scored below 100% on v1 |
| Third paid model (token budget) | `qwen3.7-plus` (thinking off, `max_tokens` 4096) | synthetic suite (v1 + v2 healing) on **44 of the 47 sites** (all but shop-06, shop-08, homes-05), direct extraction on the 8-site subset, schema on 15 sites, plus the first 2-call smoke test; 494,815 of a 500,000-token budget |

Files: `haiku_rerun_*.json` (headline Haiku numbers), `haiku_*.json` (the first pass, see "Errors" below),
`opus_synthetic.json`, `opus_hard_synthetic.json`, `qwen_all_synthetic.json` (the 44 Qwen sites folded together from
`qwen_synthetic.json` and `qwen_b1`...`qwen_b7_synthetic.json`), `qwen_incomplete-shop-06_synthetic.json` (left out, see
"Errors"), `qwen_direct.json`, `qwen_schema.json`, `qwen_smoke_*.json`, and the paid call ledger `calls.jsonl`
(model ids, status, latency, tokens; no prompts).

## 1. Full evaluation set: free baseline vs Haiku (47 sites)

| Metric | Free `nemotron-3-super` | `claude-haiku-5-5` |
|---|---|---|
| v1 field accuracy | 99.2% (7,594 / 7,655) | **99.3%** (7,599 / 7,655) |
| v1 record precision / recall | 100% / 100% (1,132 records) | 100% / 100% |
| Extractor learning: rules accepted (37 learned sites) | 37 / 37 (34 on the first attempt) | 37 / 37 (**37** on the first attempt) |
| v1 model calls | 55 | 52 |
| v2 redesign: change detected | 37 / 37 | 37 / 37 |
| Self-heal verified | 31 / 37 | **34 / 37** |
| Records found after healing (recall) | 95.2% (987 / 1,037) | **100%** (1,037 / 1,037) |
| Field accuracy of found records after healing | **99.8%** (6,905 / 6,917) | 99.5% (7,223 / 7,259) |
| Heal calls + direct-fallback calls | 58 + 15 | 47 + 8 |
| False layout-change alarms on unchanged v1 | 0 | 0 |
| Direct extraction (49 first pages): record recall | 88.2% (528 / 599) | **100%** (599 / 599) |
| Direct extraction: field accuracy | 97.7% (3,334 / 3,412) | 98.4% (3,838 / 3,902) |
| Schema from intent: requested fields found | 313 / 314 | 313 / 314 |
| Schema from intent: exactly the right type | 282 / 314 (89.8%) | **292 / 314 (93.0%)** |
| Traps: hidden decoys / planted PII in output | 0 of 4 / 0 of 32 | 0 of 4 / 0 of 32 |
| Traps: currencies / relative dates right | 499 / 499, 286 / 286 | 499 / 499, 284 / 286 |
| Prompt injection: values zeroed, fields injected (4 sites, guard on and off) | 0, 0 | 0, 0 |
| books.toscrape / quotes.toscrape (snapshots, 0 live requests) | 60/60 and 100/100 at 100% | 60/60 and 100/100 at 100% |
| Latency p50: learning call / heal call / direct page | 17.7 s / 25.4 s / 25.8 s | **6.7 s / 8.5 s / 7.1 s** |
| Cost of the run | $0 (free tier) | about **$0.21** (498 k input, 324 k output tokens over all suites) |

## 1b. Qwen on 44 of the 47 sites (free and Haiku restricted to the same 44)

The three sites missing are shop-06, shop-08 and homes-05 (the token budget ran out; see "Errors"). The 44 cover every
vertical and mode: 11 shops (2 JavaScript, 1 load-more), 7 job boards, 6 property sites, 6 event calendars,
5 menus (2 PDFs), 5 SaaS pricing pages, 4 registers, and all 7 sites of the hard subset.

| Metric | Free `nemotron-3-super` | `claude-haiku-5-5` | `qwen3.7-plus` |
|---|---|---|---|
| v1 field accuracy | 99.1% (6,946 / 7,007) | **99.2%** (6,951 / 7,007) | 96.2% (6,737 / 7,007) |
| v1 record precision / recall | 100% / 100% | 100% / 100% | 100% / 100% |
| Extractor learning: templates with accepted rules | 34 / 34 (32 first attempt) | 34 / 34 (34 first attempt) | 33 / 34 (32 first attempt; jobs-06 fell back to direct) |
| v1 model calls | 51 | 49 | 52 |
| v2 redesign: change detected | 34 / 34 | 34 / 34 | 33 / 33 |
| Self-heal verified | 28 / 34 | **31 / 34** | 19 / 33 |
| Records found after healing | 94.9% (899 / 947) | **100%** (947 / 947) | 95.7% (881 / 921) |
| Field accuracy after healing | **99.9%** (6,274 / 6,283) | 99.5% (6,576 / 6,611) | 93.0% (5,671 / 6,097) |
| Heal calls + direct-fallback calls | 54 + 15 | 44 + 8 | 67 + 36 |
| False layout-change alarms | 0 | 0 | 0 |
| Latency p50: learning / heal call | 17.7 s / 25.4 s | 6.7 s / 8.5 s | 13.4 s / 13.8 s |

v1 field accuracy by vertical (free / Haiku / Qwen): shops 100 / 100 / 95.0%, job boards 100 / 99.9 / 98.2%,
property 99.2 / 100 / 98.5%, events 99.9 / 99.9 / 99.0%, registers 95.9 / 95.9 / 95.9%, menus 100 / 100 / **84.5%**,
SaaS 100 / 100 / **85.0%**.

On the 15 sites Opus ran (8 comparison + 7 hard), with the same restriction:

| Metric | Free | Haiku | Opus | Qwen |
|---|---|---|---|---|
| v1 field accuracy | 97.8% | 98.1% | **100.0%** (2,818 / 2,819) | 95.0% |
| Self-heal verified | 8 / 14 | 12 / 14 | **14 / 14** | 7 / 14 |
| Records found after healing | 87.5% | 100% | 100% | 89.6% |
| Field accuracy after healing | 99.6% | 99.8% | **100.0%** | 92.0% |

## 2. The README's 8-site model comparison (shop-02, shop-07, jobs-03, homes-01, events-02, menu-03, registry-01, spa-shop-06)

| Model | v1 field accuracy | Heals verified | Accuracy after healing | Calls (v1) | Learning call p50 |
|---|---|---|---|---|---|
| free `nemotron-3-super` (main run) | 100% | 5 of 7 | 100% (two sites through the direct fallback) | 10 | 17.7 s |
| free `nemotron-3-ultra` | 100% | 7 of 7 * | 98.4% | 10 | 60.8 s |
| free `dots-3-note-preview` | 98.4% | 6 of 7 * | 95.8% (1 false alarm) | 17 | 38.7 s |
| `claude-haiku-5-5` | 100% | 6 of 7 | 99.8% | 10 | 6.7 s |
| `claude-opus-5-5` | 100% | **7 of 7** | **100%** | 10 | 9.1 s |
| `qwen3.7-plus` | 94.7% (1,385 / 1,463) | 4 of 7 | 91.5% (1,283 / 1,403) | 9 | 13.2 s |

\* ultra and dots were run before the list-of-objects verifier fix (see the README); with it, one of their heals would
have failed verification. Haiku, Opus and the main free run use the fixed verifier.

## 3. Hard subset for Opus: 7 sites the free model got wrong (jobs-02, jobs-05, registry-02, scroll-shop-08, homes-02, events-03, scroll-homes-05)

| Metric | Free `nemotron-3-super` | `claude-haiku-5-5` | `claude-opus-5-5` | `qwen3.7-plus` |
|---|---|---|---|---|
| v1 field accuracy | 95.5% (1,295 / 1,356) | 96.0% (1,302 / 1,356) | **99.9%** (1,355 / 1,356) | 95.3% (1,292 / 1,356) |
| Self-heal verified | 3 / 7 | 5 / 7 | **7 / 7** | 3 / 7 |
| Records found after healing | 74.5% (140 / 188) | 100% (188 / 188) | 100% (188 / 188) | 78.7% (148 / 188) |
| Field accuracy after healing | 99.1% | 99.7% | **99.9%** | 92.8% (998 / 1,076) |
| Heal + fallback calls | 16 + 8 | 9 + 4 | 7 + 0 | 15 + 8 |
| Model cost on this subset | $0 | about $0.02 | $0.47 | about 102 k tokens |

## 4. Qwen on the 8-site subset: direct extraction and schema

Same eight sites as section 2 (nine pages: the PDF menu takes two). Free and Haiku numbers are their full-set runs
restricted to these sites.

| Metric | Free `nemotron-3-super` | `claude-haiku-5-5` | `qwen3.7-plus` |
|---|---|---|---|
| Direct: record recall | 97.1% (101 / 104) | 100% | 100% |
| Direct: field accuracy | 97.5% (649 / 666) | **98.3%** (672 / 684) | 89.5% (612 / 684) |
| Direct: call latency p50 | 25.8 s (full suite) | 7.1 s (full suite) | 22.4 s |
| Schema (15 sites, 100 requested fields): found / right type | 100 / 90 | 99 / 93 | **100 / 94** |
| Schema: extra fields, injected fields | 0, 0 | 4, 0 | 0, 0 |

Qwen's schema sites: the eight above plus saas-02, jobs-02, homes-02, events-03, registry-02, menu-05 and shop-03.

## 5. First Qwen smoke test (2 calls, 3,000-token budget; kept for the record)

| Task | Free `nemotron-3-super` | `claude-haiku-5-5` | `qwen3.7-plus` |
|---|---|---|---|
| Direct extraction, `saas-02` (3 plans, prices in EUR) | 3/3 records, 100% fields | 3/3, 100% | 3/3, **60%**: both price fields kept as USD 19 / 15 instead of EUR (converted: 20.52 / 16.20) |
| Schema proposal, `menu-03` (4 fields) | 4 found, 4 right type | 4, 4 | 4 found, 3 right type (`section` proposed as an enum) |
| Latency | 25.8 s / 9.4 s p50 (suite) | 7.1 s / 5.3 s p50 (suite) | 8.1 s / 8.2 s |

## Cost

| Model | Requests (ok) | Input / output tokens | Cost |
|---|---|---|---|
| `claude-haiku-5-5` | 216 (209) | 498,380 / 323,974 | $0.21 at $0.10 / $0.50 per M |
| `claude-opus-5-5` | 33 (33) | 92,669 / 28,866 | $0.95 at $4 / $20 per M (8-site subset $0.48, hard subset $0.47) |
| `qwen3.7-plus` | 181 (177) | 347,379 / 127,790 | **494,815** of the 500,000-token Qwen budget (proxy count, which runs about 4% above the ledger; smoke test included) |
| **Proxy total for this project (Anthropic)** | | | **$1.28** of the $3.50 cap after the Haiku and Opus runs (the proxy's own figure; about $0.12 above the ledger estimate, part of it the 2-call Qwen smoke test) |

Haiku's figure includes 6 trial calls (2 sites) and the 6 retried calls of the re-run; everything else was answered from
the disk cache.

## Errors and how the numbers were produced

- **Haiku, 7 failed requests of 216**: 5 answered `403 Request not allowed` by the gateway within 0.2 s (one learning
  call, one heal call, one direct page, two schema proposals; on unrelated pages, so not a content pattern I can name)
  and 2 `502` that the budget wrapper retried. The first pass (`haiku_*.json`) therefore lost two schema proposals and
  one direct page. The suites were then re-run under the name `haiku_rerun`: the disk cache answered every call that
  had succeeded, and only the 6 calls behind the failures were made again (all succeeded). The tables use the re-run;
  first-pass numbers were v1 99.0%, 33 of 37 heals, direct recall 93.3%, schema 302 of 314 found.
- **Qwen, second round** (budget raised to 150,000 tokens): 54 calls, all succeeded, none cut off at the 4096-token
  cap, no retries needed (`SIFT_LLM_MAX_RETRIES=0`, to avoid retrying a budget 429). The full set did not fit (Haiku's
  run used about 820 k tokens), so Qwen ran the 8-site comparison subset (synthetic suite and direct) and schema on 15
  sites; it stopped with about 7 k tokens left.
- **Qwen, third round** (budget raised to 500,000 tokens): the synthetic suite ran in batches on 36 more sites (the
  hard subset first, then a mix of verticals), checking the budget before each batch. 124 calls; failures: the
  infinite-scroll shop's heal-fallback page (40 items) was **cut off at the proxy's 4,096-token cap twice**, so that site
  recovered 0 records after the redesign (a cap artifact as much as a model failure); and shop-06's fifth learning call
  was refused by the budget (429 at 494,815 / 500,000), so that site was left out of every table
  (`qwen_incomplete-shop-06_synthetic.json`). shop-08 and homes-05 were not run.
- **Qwen, smoke test**: the first schema call was refused by the proxy before it was sent (it reserves `max_tokens` against the
  token budget); it was sent once more with `SIFT_LLM_MAX_TOKENS=600` and succeeded.
- **Latency** comes from the call ledgers (requests that reached the API, not cache hits). The baseline's figures cover
  all of its real calls on 2026-10-12, including the runs before its bug fixes.
- toscrape replayed the committed-run snapshots (`data/snapshots/`, 81 responses): **0 requests** went to the live
  sites. The synthetic web is served from disk; robots.txt, Crawl-delay and the PII guard ran as in the baseline
  (0 robots-disallowed pages fetched, 2 refusals logged, 0 planted PII leaked).

## Observations

- **Self-healing is where the stronger models pay off.** Free: 31 of 37 heals verified and 95.2% of the v2 records
  recovered. Haiku: 34 of 37 and 100% of the records, with fewer calls (55 vs 73). Opus verified every heal on the 14
  learned sites it ran, including all six that the free model could not heal (two job boards, a property site, the
  register, the JavaScript shop, and the infinite-scroll shop, where the free model's direct fallback returned nothing).
- **First-pass extraction was already solved by the free model.** v1 field accuracy is 99.2% (free) vs 99.3% (Haiku),
  and record precision and recall are 100% for both. Most of the remaining errors are not the model's fault: SKUs that
  are only on product pages direct mode never opens, and a ground-truth convention (`North` vs `North district`) that
  only Opus happened to match.
- **Direct extraction is more reliable with Haiku.** The free model returned no valid JSON on 3 of 49 pages (two shops
  and the 40-item infinite-scroll page). Haiku answered all 49: recall went from 88.2% to 100%, at 3.6 times lower
  latency (p50 7.1 s vs 25.8 s), because Haiku does not spend thousands of tokens reasoning first.
- **Better schema types.** Haiku got 292 of 314 field types exactly right vs 282: it fixed 16 (employment type as an
  enum on 7 job boards, seat counts as integers, bathrooms as numbers) and broke 6 (menu sections as enums, a register
  contact as an object). It again proposed 0 injected fields.
- **Small regressions with Haiku:** after healing, field accuracy is 99.5% vs 99.8%. One healed SPA shop lost half-star
  ratings (4.5 read as 4.0 on 5 products, and the heal still passed verification); on one job board the heal failed
  verification and the direct fallback left 3 "Remote" locations empty; on one shop the fallback lost the SKUs that
  only detail pages have; and 2 of 286 relative "posted" dates came back empty. The injection guard flagged the same 4 sites, and the model changed no value either way.
- **Qwen 3.7 Plus (no thinking) is the weakest model here, below the free baseline**, and the 44-site run confirms the
  8-site picture: v1 field accuracy 96.2% (free 99.1%, Haiku 99.2%), 19 of 33 heals verified (free 28 of 34, Haiku
  31 of 34), 93.0% field accuracy after healing (free 99.9%, Haiku 99.5%), with the most fallback calls (36). It found
  every record on v1 (precision and recall 100%) and learned rules for 33 of 34 templates, so the losses are in values
  and in healing:
  - **Currencies in direct mode**: every non-USD one-page site lost all its prices (3 menus, 2 SaaS pages: amounts
    labelled USD instead of EUR or GBP), which is why menus and SaaS score about 85%. With learned rules the same
    currencies came out right, because the rules capture the price text and Sift's normalizer reads the symbol.
    On the 8 direct pages it also lost prices on EUR shop and event pages (89.5% vs free 97.5%, Haiku 98.3%).
  - **Detail pages**: on the three shops whose SKUs exist only on product pages (shop-03, shop-07, shop-10) it never
    asked for product-page rules, so all 72 SKUs stayed empty. Free, Haiku and Opus learned those rules.
  - **Learning**: one job board (jobs-06) got no valid rules after 3 attempts and went to direct extraction, which
    returned no posting dates (0 of 26).
  - **Variants and decimals**: variant names came back empty or cut short ("One" instead of "One size"). Bathroom rules
    on three property sites read "5" from "1.5". One shop's ratings were wrong on 7 of 30 products.
  - **Weak heals**: 14 of 33 heals failed verification, and their direct fallbacks lost values. Job boards lost
    salaries and dates (59% and 70% after healing), shops lost SKUs and exact prices (798.99 became 800), and the
    infinite-scroll shop hit the 4,096-token cap and recovered nothing. One heal that passed verification read the
    wrong price on 7 of 32 products of the JavaScript shop.
  - **Strong schema proposals**: 100 of 100 fields found, 94 with the right type (free 90, Haiku 93). It is about as
    fast as Haiku on schema calls (8.8 s p50) but around 2x slower on learning and healing (13 to 14 s).
- **Sample sizes:** Haiku ran the full set. Qwen ran 44 of 47 sites for the synthetic suite (comparable with Haiku's
  table when both are restricted to those 44) but only 8 sites for direct extraction and 15 for schema. Opus ran 15 sites
  (8 + 7, chosen because they are the comparison set and the free model's failures, so the hard subset favours a
  stronger model by construction). For the cost, Haiku is the clear default upgrade
  (about $0.21 for every suite). Opus's extra accuracy cost about 30 times more per call than Haiku on these sites.

## Reproduce

```bash
export SIFT_LLM_PROVIDER=compat SIFT_LLM_BASE_URL=http://127.0.0.1:8787/v1 SIFT_LLM_API_KEY=<gateway key>
export SIFT_ALLOW_PAID_MODELS=true SIFT_LLM_SEND_TEMPERATURE=false SIFT_LLM_FALLBACK_MODELS=
export SIFT_RESULTS_DIR=results/paid-models SIFT_LLM_LEDGER=results/paid-models/calls.jsonl SIFT_LLM_MAX_CALLS=700
export SIFT_LLM_MIN_SECONDS_BETWEEN_REQUESTS=1 SIFT_BROWSER_EXECUTABLE=/usr/bin/google-chrome
SIFT_LLM_MODEL=claude-haiku-5-5 uv run sift eval synthetic --name haiku_rerun   # then direct, schema, traps, toscrape
SIFT_LLM_MODEL=claude-opus-5-5 uv run sift eval synthetic --name opus \
  --sites shop-02,shop-07,jobs-03,homes-01,events-02,menu-03,registry-01,spa-shop-06
SIFT_LLM_MODEL=claude-opus-5-5 uv run sift eval synthetic --name opus_hard \
  --sites jobs-02,jobs-05,registry-02,scroll-shop-08,homes-02,events-03,scroll-homes-05
export Q="SIFT_LLM_MODEL=qwen3.7-plus SIFT_LLM_MAX_TOKENS=4096 SIFT_LLM_MAX_RETRIES=0"
S8=shop-02,shop-07,jobs-03,homes-01,events-02,menu-03,registry-01,spa-shop-06
env $Q uv run sift eval synthetic --name qwen --sites $S8
# then batches qwen_b1 ... qwen_b7 of the remaining sites (hard subset first), folded into qwen_all_synthetic.json
env $Q uv run sift eval direct --name qwen --sites $S8 --workers 2
env $Q uv run sift eval schema --name qwen --workers 1 \
  --sites $S8,saas-02,jobs-02,homes-02,events-03,registry-02,menu-05,shop-03
SIFT_LLM_MODEL=qwen3.7-plus uv run sift eval direct --name qwen_smoke --sites saas-02 --workers 1
SIFT_LLM_MODEL=qwen3.7-plus SIFT_LLM_MAX_TOKENS=600 uv run sift eval schema --name qwen_smoke --sites menu-03 --workers 1
```
