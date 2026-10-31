# fj-audit

Read every FLAPJAX transfer out of the FlapJax treasury for one COT day and group the
recipients by the wallet that first sent them BNB. Every number can be re-checked by a
stranger against a public BSC node.

## Why two sources

A public BSC endpoint answers an empty log array for a block range it does not index, and
`null` for a receipt it cannot find. Both look like "no such transfer" and both are wrong,
and that is what makes a treasury audit wrong without looking wrong.

The tool scans the transfer set on one provider and re-reads every receipt on a second one.
It refuses to print a total unless the two agree on the tx set and on the sum. Two
independent `eth_getLogs` providers is not achievable on free BSC endpoints: defibit and
bnbchain cap the block range, and publicnode and 48.club answer an empty array for a range
that does contain transfers. A receipt re-read on a second provider still catches a wrong
transfer set, because the receipt carries the value and the recipient.

## Install

Python 3.9 or newer, standard library only. No install step.

```bash
git clone <this repo>
cd fj-audit
python3 -m unittest test_fj_audit -v
```

## Use

```bash
# One COT day (UTC-5, no DST): D runs from D-1 05:00Z to D 05:00Z
python3 fj_audit.py day --date 2026-10-30 --out report.json
# With no --date it audits yesterday COT, which is the day the board asks about most often
python3 fj_audit.py day --out report.json

# An explicit block window, when the day boundaries are already known
python3 fj_audit.py range --from-block 130605333 --to-block 130797288 --out report.json

# First BNB funder per recipient, then the funder groups
python3 fj_audit.py funders --rows report.json --out funders.json
```

Behind an egress-restricted network, pass `--proxy http://127.0.0.1:7890` to any subcommand.

## Output

`range` writes:

```json
{
  "ok": true,
  "from_block": 130605333,
  "to_block": 130797288,
  "tx_count": 14,
  "recipients": 11,
  "total_raw": "1145000000000000000000000",
  "total_fj": "1145000.000000",
  "rows": [{"tx": "0x…", "block": 130605437, "status": "0x1",
            "to": "0x…", "raw": "250000000000000000000000"}],
  "missing_receipts": []
}
```

`funders` adds `funders` (per recipient: the funder tx, the block, or `unfunded`) and
`groups` (recipients that share a first funder, plus the funder tx that links each group).

## What the exit codes mean

| code | meaning |
|---|---|
| `0` | both providers returned the same transfer set and every receipt was read |
| `4` | the providers disagreed, a receipt was missing, or fewer than two answered |
| `2` | bad usage |

A `4` is not a statement about the treasury. It is a statement that this run could not
establish one, and the JSON says which provider failed or which tx had no receipt.

## Grouping rule

Recipients are grouped by the wallet that first sent them BNB. A recipient that never held
BNB (paid through a paymaster) is reported as `unfunded` and gets its own group. Putting it
in another group would be a guess, not a read, and the report says so instead.

## The treasury it reads

FlapJax treasury `0x7C34E9e21eE28A49Ff0b84B61774119E6633359f`, token FLAPJAX
`0x90c8889f428F9Ebb77BB8f15CAD3a50a9aC680df`, BSC (chainId 56), 18 decimals.

## License

MIT. See LICENSE.
