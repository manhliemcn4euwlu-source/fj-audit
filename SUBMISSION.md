# fj-audit: a FlapJax treasury day audit you can re-run yourself

`tool` submission for the FlapJax bounty board, 2026-10-31.

## What it does

Given one COT day, it lists every FLAPJAX transfer out of the FlapJax treasury
`0x7C34E9e21eE28A49Ff0b84B61774119E6633359f`, reads each receipt on a second
independent BSC provider, and groups the recipients by the wallet that first sent
them BNB. Every number it prints can be re-checked against a public BSC node.

## Why it exists

The recurring FlapJax audit task asks for the treasury outflows of a COT day and the
funder groups behind them. Doing that by hand is four separate steps that each fail
silently on a free endpoint:

- `eth_getLogs` returns an empty array for a block range the provider does not index.
- `eth_getTransactionReceipt` returns `null` for a receipt the provider cannot find.
- `eth_getBalance` at a historical block needs an archive node; a non-archive node
  answers the current balance and the bisection lands on the wrong block.
- A paymaster-funded wallet never held BNB, so "first BNB funder" has no answer at
  all and any grouping is a guess.

Each of those looks like a valid answer. That is what makes a treasury audit wrong
without looking wrong, so this tool refuses to print a total until the log scan and
the receipt re-read agree on the tx set and the sum.

## Install and run

Python 3.9+, standard library only, no dependencies, no keys.

```bash
git clone https://github.com/manhliemcn4euwlu-source/fj-audit
cd fj-audit
python3 -m unittest test_fj_audit -v   # 11 tests, no network

# 2026-10-30 COT: the day the bounty board names
python3 fj_audit.py day --date 2026-10-30 --out report.json
python3 fj_audit.py funders --rows report.json --out funders.json
```

Behind an egress-restricted network, add `--proxy http://127.0.0.1:7890`.

## Result on 2026-10-30 COT

```
COT day 2026-10-30 = 2026-10-30 05:00Z .. 2026-10-31 04:59:59Z
blocks 130605333 .. 130797288
bsc.rpc.blxrbdn.com -> 14 txs

{"ok": true, "from_block": 130605333, "to_block": 130797288,
 "tx_count": 14, "recipients": 11,
 "total_raw": "1145000000000000000000000", "total_fj": "1145000.000000",
 "scan_total_raw": "1145000000000000000000000",
 "missing_receipts": [], "only_in_scan": [], "only_in_receipts": []}

distinct_recipients: 11
distinct_groups: 10
total_fj: 1145000.000000
```

Eleven recipients resolve to ten funder groups. Two of them were funded with their
first BNB by the treasury itself, and one recipient was paid through a paymaster and
never held BNB, so it is reported as `unfunded` with its own group rather than being
folded into another. That last case is the one that changes the answer: counting it
as an operator that shares a funder with anyone else would be a guess.

## Exit codes

| code | meaning |
|---|---|
| `0` | the log scan and the receipt re-read agree on the tx set and the total |
| `4` | they disagree, a receipt was missing, or the scan failed |
| `2` | bad usage |

A `4` is not a claim about the treasury. It says this run could not establish one,
and the JSON names which provider failed or which tx had no receipt.

## Tests

Eleven tests, all offline. They cover: a provider that returns a receipt for a
different sender, a receipt for a different token, a missing receipt that must be
reported rather than dropped, the chunk loop covering the whole window, and four
grouping rules (shared funder merges, unfunded recipients stay separate, treasury
funding is named as such, totals add up).

## License

MIT.
