"""Frozen notice facts -> account-dependent events on the L2 account timeline.

The original cash-dividend adapter supplies the accounting/tax policy. This
module only schedules it and persists completed stages in the account's own
transaction. It neither verifies announcements nor queries a provider.
"""
from decimal import Decimal, ROUND_CEILING
import hashlib
from pathlib import Path

from .kernel import FROZEN, module
from .l2_execution_evidence import local_time

CA = module('corporate_action_adapter')


def source_pins():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (Path(__file__), Path(__file__).with_name('l2_execution_evidence.py'),
             FROZEN / 'corporate_action_adapter.py')}


class CorporateSchedule:
    def __init__(self, plan):
        specs = plan.get('corporate_announcements', [])
        if type(specs) is not list or len(specs) > 128:
            raise ValueError('bounded explicit announcement list required')
        self.adapters = {}
        self.stages = []
        policy = plan.get('corporate_policy', {})
        if specs and set(policy) != {'rounding_policy', 'boundary_policy', 'cash_reserve'}:
            raise ValueError('explicit corporate simulation policies required')
        if specs and policy['cash_reserve'] != 'maximum_registered_tax_until_final':
            raise ValueError('unsupported intraday tax cash reserve')
        static_ids = {e['data'].get('action_id') for e in plan['corporate_events']}
        for item in specs:
            spec = CA.CashDividendAnnouncement(**item)
            if spec.symbol not in plan['codes']:
                raise ValueError('announcement security outside frozen scope')
            if spec.action_id in self.adapters or spec.action_id in static_ids:
                raise ValueError('duplicate economic action in static/dynamic schedules')
            adapter = CA.CashDividendAdapter(spec, plan['calendar'],
                rounding_policy=policy['rounding_policy'], boundary_policy=policy['boundary_policy'])
            manifest = adapter.manifest()
            if local_time(manifest['record_at']) < local_time(plan['initial_time']):
                raise ValueError('registration predates account history; no inferred opening rights')
            self.adapters[spec.action_id] = adapter
            for rank, (kind, key) in enumerate((('record','record_at'),
                                               ('activate','activate_at'), ('cash','cash_available_at'))):
                self.stages.append((local_time(manifest[key]), rank, spec.action_id, kind))
            for day in plan['calendar']:
                if day >= spec.ex_date:
                    self.stages.append((local_time(day+'T15:05:00+08:00'), 3, spec.action_id, 'tax'))
        self.stages.sort()
        if len(self.stages)>8192:
            raise ValueError('corporate schedule exceeds 8192 bounded stages')

    def manifests(self):
        return [self.adapters[key].manifest() for key in sorted(self.adapters)]

    def cash_reserve(self, action_id, action):
        """A numerical ceiling only for the explicitly admitted cash-only rule.

        Do not use the provisional assessed amount as the ceiling while shares
        remain held. This reserve is not actual tax or a cash debit.
        """
        adapter = self.adapters.get(action_id)
        if adapter is None:
            raise ValueError('unresolved tax liability prevents spending cash')
        facts = adapter.manifest()['announcement']
        ceiling = (Decimal(facts['gross_cash_per_share']) * action['entitled_shares'] *
                   Decimal(facts['short_holding_tax_rate'])).quantize(Decimal('.01'), rounding=ROUND_CEILING)
        if action['tax_due'] is not None and action['tax_due'] > ceiling:
            raise ValueError('assessed tax exceeds frozen cash-only tax ceiling')
        return max(Decimal(0), ceiling - action['tax_paid'])

    def due(self, db, clock):
        completed = {x[0] for x in db.execute('SELECT id FROM corporate_stages')}
        return [(at, rank, aid, kind, f'{aid}:{kind}:{at.isoformat()}')
                for at, rank, aid, kind in self.stages if at <= clock and
                f'{aid}:{kind}:{at.isoformat()}' not in completed]

    def apply(self, account, stage, encode, fault=None):
        at, _, aid, kind, key = stage
        ledger, db = account.ledger, account.db
        adapter = self.adapters[aid]
        if ledger._last_applied and ledger._last_applied > at:
            raise ValueError('unprocessed corporate stage behind account clock; no historical reconstruction')
        before = ledger._previous_hash
        first = len(ledger._journal)
        action = ledger._state['actions'].get(aid)
        skipped = None
        def apply(event):
            ledger.apply(event, as_of=at.isoformat())
        if kind == 'record':
            held = sum(l['quantity'] for l in ledger._state['lots'].values()
                       if l['symbol'] == adapter.manifest()['announcement']['symbol'])
            if held:
                apply(adapter.record_event(ledger))
            else:
                skipped = 'no_shares_at_registration'
        elif action is None:
            skipped = 'no_registered_rights'
        elif kind == 'activate':
            apply(adapter.activation_event(ledger))
        elif kind == 'cash':
            for event in adapter.settlement_events(ledger):
                apply(event)
        elif kind == 'tax':
            adapter.validate_eod_integrity(ledger)
            if not action['tax_final']:
                apply(adapter.tax_assessment_event(ledger, as_of=at.isoformat()))
            action = ledger._state['actions'][aid]
            if action['cash_paid'] and action['tax_due'] is not None and action['tax_due'] > action['tax_paid']:
                apply(adapter.tax_payment_event(ledger, as_of=at.isoformat()))
        else:
            raise ValueError('unregistered corporate stage')
        events = ledger._journal[first:]
        receipt = {'id':key, 'action_id':aid, 'stage':kind, 'at':at.isoformat(),
                   'input_head':before, 'output_head':ledger._previous_hash,
                   'event_ids':[e['event']['event_id'] for e in events], 'skipped':skipped,
                   'source_sha256':adapter.manifest()['announcement']['source_sha256'],
                   'assumption_sha256':adapter.manifest()['assumption_sha256'],
                   'execution_valid':False}
        for entry in events:
            db.execute('INSERT INTO events VALUES(?,?)',(entry['sequence'],encode(entry)))
        if fault:
            fault('corporate_before_receipt')
        db.execute('INSERT INTO corporate_stages VALUES(?,?)',(key,encode(receipt)))
        return len(events)
