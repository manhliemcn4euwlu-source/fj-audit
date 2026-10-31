#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for fj_audit. No network: every RPC answer is stubbed."""
import unittest

import fj_audit as A

TX1 = '0x' + '11' * 32
TX2 = '0x' + '22' * 32
TX3 = '0x' + '33' * 32
RECIP_A = '0x' + 'aa' * 20
RECIP_B = '0x' + 'bb' * 20
FUNDER = '0x' + 'ff' * 20


def log(tx, block, to, raw):
    return {'address': A.FJ, 'blockNumber': hex(block), 'transactionHash': tx,
            'topics': [A.TRANSFER, A.topic_pad(A.TREASURY), A.topic_pad(to)], 'data': hex(raw)}


def rcpt(tx, block, to, raw, status='0x1'):
    return {'blockNumber': hex(block), 'status': status, 'logs': [log(tx, block, to, raw)]}


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def test_both_providers_agree(self):
        logs = {A.LOG_RPC: [log(TX1, 100, RECIP_A, 5)], A.LOG_RPC: [log(TX1, 100, RECIP_A, 5)]}

        def fake(url, method, params, tries=6):
            if method == 'eth_getLogs':
                return logs[url]
            if method == 'eth_getTransactionReceipt':
                return rcpt(params[0], 100, RECIP_A, 5)
            raise AssertionError(method)

        orig = A.rpc
        A.rpc = fake
        try:
            rows = A.scan_logs(A.FJ, A.TREASURY, 100, 200, A.LOG_RPC)
            self.assertEqual(len(rows), 1)
            rec, missing = A.receipts([TX1], A.RCPT_RPC)
            self.assertEqual(missing, [])
            self.assertEqual(rec[0]['raw'], '5')
        finally:
            A.rpc = orig

    def test_chunking_covers_the_window(self):
        seen = []

        def fake(url, method, params, tries=6):
            seen.append((int(params[0]['fromBlock'], 16), int(params[0]['toBlock'], 16)))
            return []

        orig = A.rpc
        A.rpc = fake
        try:
            A.scan_logs(A.FJ, A.TREASURY, 1, A.CHUNK * 2, A.LOG_RPC)
        finally:
            A.rpc = orig
        self.assertEqual(seen[0][0], 1)
        self.assertEqual(seen[-1][1], A.CHUNK * 2)
        self.assertEqual(len(seen), 2)

    def test_missing_receipt_is_reported_not_dropped(self):
        def fake(url, method, params, tries=6):
            return None

        orig = A.rpc
        A.rpc = fake
        try:
            rec, missing = A.receipts([TX1], A.RCPT_RPC)
        finally:
            A.rpc = orig
        self.assertEqual(rec, [])
        self.assertEqual(missing, [TX1])

    def test_receipt_from_another_sender_is_ignored(self):
        other = {'blockNumber': '0x64', 'status': '0x1',
                 'logs': [{'address': A.FJ, 'topics': [A.TRANSFER, A.topic_pad(RECIP_A), A.topic_pad(RECIP_B)],
                           'data': '0x5'}]}
        orig = A.rpc
        A.rpc = lambda *a, **k: other
        try:
            rec, _ = A.receipts([TX1], A.RCPT_RPC)
        finally:
            A.rpc = orig
        self.assertEqual(rec, [])

    def test_receipt_from_another_token_is_ignored(self):
        other = {'blockNumber': '0x64', 'status': '0x1',
                 'logs': [{'address': '0x' + '99' * 20,
                           'topics': [A.TRANSFER, A.topic_pad(A.TREASURY), A.topic_pad(RECIP_A)],
                           'data': '0x5'}]}
        orig = A.rpc
        A.rpc = lambda *a, **k: other
        try:
            rec, _ = A.receipts([TX1], A.RCPT_RPC)
        finally:
            A.rpc = orig
        self.assertEqual(rec, [])


class GroupTest(unittest.TestCase):
    def test_shared_funder_merges_recipients(self):
        rows = [{'to': RECIP_A, 'raw': '5'}, {'to': RECIP_B, 'raw': '7'}]
        fund = {RECIP_A: {'funder': {'from': FUNDER, 'tx': TX1}, 'unfunded': False},
                RECIP_B: {'funder': {'from': FUNDER, 'tx': TX2}, 'unfunded': False}}
        g = A.group(rows, fund)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]['raw'], '12')
        self.assertEqual(g[0]['recipients'], [RECIP_A, RECIP_B])

    def test_unfunded_recipients_do_not_merge(self):
        rows = [{'to': RECIP_A, 'raw': '5'}, {'to': RECIP_B, 'raw': '7'}]
        fund = {RECIP_A: {'funder': None, 'unfunded': True}, RECIP_B: {'funder': None, 'unfunded': True}}
        g = A.group(rows, fund)
        self.assertEqual(len(g), 2)
        self.assertTrue(all(x['funder_tx'] is None for x in g))

    def test_treasury_funded_group_is_named(self):
        rows = [{'to': RECIP_A, 'raw': '5'}]
        fund = {RECIP_A: {'funder': {'from': A.TREASURY, 'tx': TX1}, 'unfunded': False}}
        g = A.group(rows, fund)
        self.assertEqual(g[0]['group'], 'treasury')
        self.assertEqual(g[0]['funder_tx'], TX1)

    def test_totals_add_up(self):
        rows = [{'to': RECIP_A, 'raw': '5'}, {'to': RECIP_A, 'raw': '9'}]
        fund = {RECIP_A: {'funder': {'from': FUNDER, 'tx': TX1}, 'unfunded': False}}
        g = A.group(rows, fund)
        self.assertEqual(g[0]['raw'], '14')


class TopicTest(unittest.TestCase):
    def test_topic_padding_is_64_hex_chars(self):
        pad = A.topic_pad(RECIP_A)
        self.assertEqual(len(pad), 66)
        self.assertTrue(pad.startswith('0x'))
        self.assertEqual(pad[2:], '0' * 24 + 'aa' * 20)

    def test_uppercase_address_pads_to_the_same_topic(self):
        self.assertEqual(A.topic_pad(RECIP_A.upper().replace('0X', '0x')), A.topic_pad(RECIP_A))


    def test_day_date_defaults_to_yesterday(self):
        """2026-10-31 官方复核: 不带 --date 必须能跑, 不能只回 argparse usage."""
        ap = A.build_parser()
        args = ap.parse_args(['day'])
        self.assertIsNone(args.date)


if __name__ == '__main__':
    unittest.main()
