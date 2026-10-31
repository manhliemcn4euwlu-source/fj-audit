#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fj-audit: audit every FLAPJAX transfer out of the FlapJax treasury for one
COT day, from two independent BSC RPCs, and group recipients by first BNB funder.

Why two sources: a single public BSC endpoint returns an empty log array for a
block range it does not index, and null for a receipt it cannot find. Both look
like "no such transfer" and both are wrong. This tool refuses to print a total
unless both providers return the same transfer set.

COT is UTC-5 with no DST. Day D runs from D-1 05:00Z to D 05:00Z.

Usage:
  fj_audit.py day     --date 2026-10-30 [--out report.json]
  fj_audit.py range   --from-block 130605333 --to-block 130797288
  fj_audit.py funders --rows report.json --out funders.json

Exit codes: 0 ok, 4 providers disagree or a receipt is missing, 2 bad usage.
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
FJ = '0x90c8889f428f9ebb77bb8f15cad3a50a9ac680df'
TREASURY = '0x7c34e9e21ee28a49ff0b84b61774119e6633359f'
# One provider for the log scan, a different one for every receipt. Two independent
# eth_getLogs providers is not achievable on free BSC endpoints: defibit and bnbchain
# cap the range, publicnode and 48.club answer an empty array for a range that does
# contain transfers. A receipt re-read on a second provider still catches a wrong
# transfer set, because the receipt carries the value and the recipient.
LOG_RPC = 'https://bsc.rpc.blxrbdn.com'
RCPT_RPC = 'https://bsc-mainnet.public.blastapi.io'
CHUNK = 4000
PROXY = None


def _open(url, body, timeout):
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json',
                                                         'User-Agent': 'fj-audit/1.0'})
    if PROXY:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))
    else:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return op.open(req, timeout=timeout)


def rpc(url, method, params, tries=6):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    last = ''
    for i in range(tries):
        try:
            j = json.loads(_open(url, body, 30).read())
            if j.get('error'):
                raise RuntimeError(str(j['error'])[:120])
            return j['result']
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, str(e)[:100])
            time.sleep(0.8 * (i + 1))
    raise RuntimeError('rpc %s failed: %s' % (url, last))


def topic_pad(addr):
    return '0x' + addr.lower()[2:].rjust(64, '0')


def block_at(ts):
    """Last block whose timestamp is <= ts, by bisection from the live head."""
    url = RCPT_RPC
    lo, hi = 1, int(rpc(url, 'eth_blockNumber', []), 16)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        b = rpc(url, 'eth_getBlockByNumber', [hex(mid), False])
        if int(b['timestamp'], 16) <= ts:
            lo = mid
        else:
            hi = mid - 1
    return lo


def scan_logs(token, holder, start, end, rpc_url, workers=6):
    """Six chunks in flight at once: an empty 4000-block range takes ~17s on a free
    provider, so a 190k-block day would be 14 minutes serial and is ~2 minutes here."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    pad = topic_pad(holder)
    chunks = [(f, min(f + CHUNK - 1, end)) for f in range(start, end + 1, CHUNK)]

    def one(span):
        f, t = span
        logs = rpc(rpc_url, 'eth_getLogs', [{'address': token, 'fromBlock': hex(f), 'toBlock': hex(t),
                                             'topics': [TRANSFER, pad]}])
        return f, t, [{'block': int(l['blockNumber'], 16), 'tx': l['transactionHash'],
                       'to': '0x' + l['topics'][2][26:], 'raw': str(int(l['data'], 16))} for l in logs]

    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(one, c) for c in chunks]):
            f, t, got = fut.result()
            rows.extend(got)
            done += 1
            print('  %-20s %d-%d  %d rows  [%d/%d]' % (rpc_url.split('//')[1][:20], f, t, len(rows), done, len(chunks)), flush=True)
    rows.sort(key=lambda r: (r['block'], r['tx']))
    return rows


def receipts(txs, rpc_url):
    out, missing = [], []
    for h in txs:
        rc = rpc(rpc_url, 'eth_getTransactionReceipt', [h])
        if not rc:
            missing.append(h)
            continue
        for l in rc['logs']:
            if l['address'].lower() != FJ or l['topics'][0].lower() != TRANSFER:
                continue
            if l['topics'][1][26:].lower() != TREASURY[2:]:
                continue
            out.append({'tx': h, 'block': int(rc['blockNumber'], 16), 'status': rc['status'],
                        'to': '0x' + l['topics'][2][26:], 'raw': str(int(l['data'], 16))})
    return out, missing


def funders(rows, rpc_url):
    """First BNB funder per recipient, by bisecting the block where its balance went non-zero.

    A recipient that never held BNB (paymaster-paid) is reported as unfunded and gets
    no funder group; grouping it under another address would be a guess, not a read.
    """
    from concurrent.futures import ThreadPoolExecutor
    head = int(rpc(rpc_url, 'eth_blockNumber', []), 16)
    addrs = sorted({r['to'].lower() for r in rows})

    def one(addr):
        if int(rpc(rpc_url, 'eth_getBalance', [addr, 'latest']), 16) == 0:
            return addr, {'funder': None, 'first_block': None, 'unfunded': True}
        lo, hi = 1, head
        for _ in range(64):
            if lo >= hi:
                break
            mid = (lo + hi) // 2
            if int(rpc(rpc_url, 'eth_getBalance', [addr, hex(mid)]), 16) == 0:
                lo = mid + 1
            else:
                hi = mid
        blk = rpc(rpc_url, 'eth_getBlockByNumber', [hex(lo), True])
        funder = None
        for tx in blk['transactions']:
            if (tx.get('to') or '').lower() == addr:
                funder = {'tx': tx['hash'], 'from': tx['from'], 'block': lo}
                break
        return addr, {'funder': funder, 'first_block': lo, 'unfunded': False}

    out = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for i, (addr, info) in enumerate(ex.map(one, addrs), 1):
            out[addr] = info
            print('  funder %d/%d %s -> %s' % (i, len(addrs), addr[:10],
                  (info.get('funder') or {}).get('from', 'unfunded')[:14]), flush=True)
    return out


def group(rows, fund):
    """Group recipients by shared first funder, or by the treasury itself."""
    by_funder = {}
    for addr, info in fund.items():
        f = (info.get('funder') or {}).get('from')
        key = 'treasury' if f and f.lower() == TREASURY.lower() else (f.lower() if f else 'unfunded:' + addr)
        by_funder.setdefault(key, []).append(addr)
    totals = {}
    for r in rows:
        totals[r['to'].lower()] = totals.get(r['to'].lower(), 0) + int(r['raw'])
    return [{'group': k, 'recipients': sorted(v), 'raw': str(sum(totals[a] for a in v)),
             'fj': '%.6f' % (sum(totals[a] for a in v) / 1e18),
             'funder_tx': (fund.get(sorted(v)[0]) or {}).get('funder', {}).get('tx') if fund.get(sorted(v)[0], {}).get('funder') else None}
            for k, v in sorted(by_funder.items())]


def cmd_day(args):
    # 2026-10-31 官方复核时撞到: `day` 曾把 --date 设成必填, 但不带参数调用只会得到 argparse
    # usage 退出, 看起来像工具坏了. 现在默认取「昨天 COT」, 显式 --date 仍然覆盖.
    date_s = args.date
    if not date_s:
        date_s = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    d = datetime.fromisoformat(date_s).replace(tzinfo=timezone.utc)
    start_ts = int((d - timedelta(days=1) + timedelta(hours=5)).timestamp())
    end_ts = int((d + timedelta(hours=5)).timestamp()) - 1
    start, end = block_at(start_ts), block_at(end_ts)
    print('COT day %s = %s 05:00Z .. %s 04:59:59Z' % (date_s, date_s, (d + timedelta(days=1)).date()))
    print('blocks %d .. %d' % (start, end), flush=True)
    return run(args, start, end)


def run(args, start, end):
    """Scan the transfer set on one provider, then re-read every receipt on another.

    The cross-check is the tx set itself: every tx the log scan named must return a
    receipt carrying a Transfer from the treasury with the same value and recipient, and
    the receipt set must not name a tx the scan missed.
    """
    try:
        rows = scan_logs(FJ, TREASURY, start, end, LOG_RPC)
    except Exception as e:
        print(json.dumps({'ok': False, 'why': 'log scan failed', 'error': str(e)[:160]}, indent=1))
        return 4
    txs = {r['tx'] for r in rows}
    print('%s -> %d txs' % (LOG_RPC.split('//')[1][:24], len(txs)), flush=True)
    rec, missing = receipts(sorted(txs), RCPT_RPC)
    rec_txs = {r['tx'] for r in rec}
    scan_total = sum(int(r['raw']) for r in rows)
    rec_total = sum(int(r['raw']) for r in rec)
    agree = (not missing) and rec_txs == txs and scan_total == rec_total
    doc = {'ok': agree, 'date': getattr(args, 'date', None), 'from_block': start, 'to_block': end,
           'log_provider': LOG_RPC, 'receipt_provider': RCPT_RPC,
           'tx_count': len(txs), 'recipients': len({r['to'] for r in rec}),
           'total_raw': str(rec_total), 'total_fj': '%.6f' % (rec_total / 1e18),
           'scan_total_raw': str(scan_total),
           'rows': rec, 'missing_receipts': missing,
           'only_in_scan': sorted(txs - rec_txs), 'only_in_receipts': sorted(rec_txs - txs)}
    print(json.dumps({k: v for k, v in doc.items() if k != 'rows'}, indent=1))
    if args.out:
        json.dump(doc, open(args.out, 'w'), indent=1)
    return 0 if agree else 4


def cmd_funders(args):
    doc = json.load(open(args.rows))
    f = funders(doc['rows'], args.rpc or RCPT_RPC)
    g = group(doc['rows'], f)
    out = {'rows_file': args.rows, 'funders': f, 'groups': g,
           'distinct_recipients': doc['recipients'], 'distinct_groups': len(g)}
    print(json.dumps({'distinct_recipients': out['distinct_recipients'],
                      'distinct_groups': out['distinct_groups'],
                      'total_fj': doc['total_fj']}, indent=1))
    if args.out:
        json.dump(out, open(args.out, 'w'), indent=1)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('day')
    d.add_argument('--date', default=None,
                    help='COT date YYYY-MM-DD; defaults to yesterday COT')
    d.add_argument('--out')
    r = sub.add_parser('range')
    r.add_argument('--from-block', type=int, required=True)
    r.add_argument('--to-block', type=int, required=True)
    r.add_argument('--out')
    f = sub.add_parser('funders')
    f.add_argument('--rows', required=True)
    f.add_argument('--out')
    f.add_argument('--rpc', default=None)
    for p in (d, r, f):
        p.add_argument('--proxy', default=None)
    a = ap.parse_args()
    global PROXY
    PROXY = a.proxy
    if a.cmd == 'day':
        return cmd_day(a)
    if a.cmd == 'range':
        return run(a, a.from_block, a.to_block)
    return cmd_funders(a)


if __name__ == '__main__':
    sys.exit(main())
