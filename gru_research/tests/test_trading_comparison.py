"""Contract/math fixtures only; no actual model forecasts or fake market prices."""
import csv
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'gru_research'))
import trading_comparison as driver
import pandas as pd
from trading_comparison_metrics import compare_run, growth, tax_growth_bounds, reference_exposures
from pretax_portfolio_replay import MarketContext


class ComparisonTests(unittest.TestCase):
    def test_excess_units_and_wealth_relative(self):
        values=growth(100,120,100,110)
        self.assertEqual(values['arithmetic_excess_percentage_points'],Decimal(10))
        self.assertEqual(values['wealth_relative_excess_return'],Decimal(120)/110-1)

    def test_unknown_tax_endpoint_is_none_not_zero(self):
        first={'net_nav_lower_bound':100,'net_nav_upper_bound':100}
        last={'net_nav_lower_bound':None,'net_nav_upper_bound':None}
        self.assertIsNone(tax_growth_bounds(first,last,100,100))
        last.update(net_nav_lower_bound=100,net_nav_upper_bound=100)
        self.assertEqual(tax_growth_bounds(first,last,100,100)['same_trading_path_net_return_lower'],0)

    def test_tax_interval_has_uncertain_year_anchor(self):
        got=tax_growth_bounds({'net_nav_lower_bound':90,'net_nav_upper_bound':100},
            {'net_nav_lower_bound':108,'net_nav_upper_bound':120},100,110)
        self.assertEqual(got['same_trading_path_net_return_lower'],Decimal('0.08'))
        self.assertEqual(got['same_trading_path_net_return_upper'],Decimal(120)/90-1)

    def fixture_result(self):
        # Arithmetic-only NAV numbers, not generated stock/index observations.
        dates=[date(2025,1,2),date(2025,12,31),date(2026,1,5),date(2026,9,23)]
        marks=[dict(date=d,nav=n,violations=[],net_nav_lower_bound=n,net_nav_upper_bound=n)
            for d,n in zip(dates,[100,120,126,132])]
        return dict(status='COMPLETE_DIAGNOSTIC_ONLY',completed_marks=marks,
            outcomes=[],provider_execution_events=[],account_event_ledger=[],failure=None),dict(zip(dates,[100,110,115,121]))

    def test_continuous_year_and_risk_block(self):
        result,bench=self.fixture_result()
        result['completed_marks'][2]['violations']=['STOCK_BELOW_90PCT']
        summary=compare_run(result,bench,account_basis='tax_upper_reserve')
        year=summary['windows']['calendar_2026_through_sep23']
        self.assertEqual(year['scenario_values']['account_scenario_return'],Decimal('0.1'))
        self.assertFalse(year['reset_account'])
        self.assertFalse(year['frozen_risk_policy_satisfied'])
        self.assertFalse(year['economic_comparison_eligible'])

    def test_incomplete_window_not_shortened_to_shared_prefix(self):
        result,bench=self.fixture_result()
        result['completed_marks'].pop()
        result.update(status='INCOMPLETE',failure=dict(error_type='MissingSource',book_day=date(2026,9,23)))
        summary=compare_run(result,bench,account_basis='pretax')
        self.assertIsNone(summary['windows']['whole_observed']['scenario_values'])
        self.assertIsNone(summary['windows']['whole_observed']['same_path_net_tax_bounds'])
        self.assertTrue(summary['prefix_diagnostic']['explicitly_diagnostic_prefix'])

    def test_turnover_uses_previous_real_nav_and_half_value(self):
        result,bench=self.fixture_result()
        result['outcomes']=[dict(date=date(2026,1,5),symbol='CONTRACT_ONLY',side='BUY',order_id='fixture',
            filled=2,price=3,fees=dict(commission=1,transfer=0,stamp=0,total=1),risk_after=None)]
        window=compare_run(result,bench,account_basis='pretax',slippage_by_order={'fixture':0})['windows']['calendar_2026_through_sep23']
        self.assertEqual(window['cumulative_gross_turnover_previous_close_nav'],Decimal('0.05'))
        self.assertEqual(window['cumulative_one_way_half_gross_turnover'],Decimal('0.025'))
        self.assertEqual(window['filled_buy_notional_cny'],6)

    def test_unknown_execution_no_trade_are_distinct(self):
        result,bench=self.fixture_result()
        result['provider_execution_events']=[dict(kind='NEXT_SESSION_RAW_TWAP_CONTEXT',day='2026-01-05',
            no_fill_windows=[dict(symbol='CONTRACT_ONLY',execution_evidence_missing=False,confirmed_no_trade_from_sourced_window_or_daily_status=True)])]
        self.assertTrue(compare_run(result,bench,account_basis='pretax')['windows']['whole_observed']['full_mark_and_execution_coverage'])
        result['provider_execution_events'][0]['no_fill_windows'][0]['execution_evidence_missing']=True
        self.assertIsNone(compare_run(result,bench,account_basis='pretax')['windows']['whole_observed']['scenario_values'])

    def test_2025_missing_required_execution_blocks_2026_carried_anchor(self):
        result,bench=self.fixture_result()
        result.update(status='INCOMPLETE',failure=dict(error_type='IncompleteRawTWAPEvidence',book_day=date(2026,9,23)))
        result['provider_execution_events']=[dict(kind='NEXT_SESSION_RAW_TWAP_CONTEXT',day='2025-12-31',
            no_fill_windows=[dict(symbol='CONTRACT_ONLY',execution_evidence_missing=True)])]
        year=compare_run(result,bench,account_basis='tax_upper_reserve')['windows']['calendar_2026_through_sep23']
        self.assertEqual(len(year['prior_execution_evidence_affect_anchor']),1)
        self.assertEqual(year['missing_execution_windows'],[])
        self.assertFalse(year['full_mark_and_execution_coverage'])
        self.assertFalse(year['economic_comparison_eligible'])
        self.assertIsNone(year['scenario_values'])
        self.assertIsNone(year['same_path_net_tax_bounds'])

    def test_only_later_2026_missing_execution_keeps_complete_2025_diagnostic(self):
        result,bench=self.fixture_result()
        result.update(status='INCOMPLETE',failure=dict(error_type='IncompleteRawTWAPEvidence',book_day=date(2026,9,23)))
        result['provider_execution_events']=[dict(kind='NEXT_SESSION_RAW_TWAP_CONTEXT',day='2026-01-05',
            no_fill_windows=[dict(symbol='CONTRACT_ONLY',execution_evidence_missing=True)])]
        summary=compare_run(result,bench,account_basis='tax_upper_reserve')
        year=summary['windows']['calendar_2025_observed']
        self.assertTrue(year['full_mark_and_execution_coverage'])
        self.assertEqual(year['prior_execution_evidence_affect_anchor'],[])
        self.assertIsNotNone(year['scenario_values'])
        self.assertIsNotNone(year['same_path_net_tax_bounds'])
        self.assertFalse(summary['windows']['calendar_2026_through_sep23']['full_mark_and_execution_coverage'])

    def test_special_reference_only_actual_exposure_and_carries_anchor(self):
        result,bench=self.fixture_result()
        result['account_event_ledger']=[dict(day='2025-01-02',kind='FILL',payload=dict(symbol='sh600004',side='BUY',quantity=100)),
            dict(day='2025-12-31',kind='SESSION_OPEN',payload={})]
        checks=[dict(symbol=s,ex_date='2025-12-31',status='DIFFERENTIAL_DIVIDEND_OR_SPECIAL_FORMULA_REQUIRES_PRIMARY')
            for s in ['sh600004','sh600005']]
        affected=reference_exposures(result,checks)
        self.assertEqual([x['symbol'] for x in affected],['sh600004'])
        summary=compare_run(result,bench,account_basis='tax_upper_reserve',special_reference_exposures=affected)
        self.assertIsNone(summary['windows']['calendar_2026_through_sep23']['same_path_net_tax_bounds'])

    def test_byte_guard_detects_changed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'contract.bin';path.write_bytes(b'read-only-fixture')
            mapping={str(path):driver.sha(path)}
            driver.verify_files(mapping)
            path.write_bytes(b'changed-fixture')
            with self.assertRaisesRegex(ValueError,'sealed bytes changed'):driver.verify_files(mapping)

    def test_required_pair_incomplete_cannot_produce_difference(self):
        result,bench=self.fixture_result()
        complete=compare_run(result,bench,account_basis='tax_upper_reserve')
        result['completed_marks'].pop()
        incomplete=compare_run(result,bench,account_basis='tax_upper_reserve')
        results={m:{s:{b:v for b in ['pretax','tax_upper_reserve']} for s in ['primary','stress']}
            for m,v in [('pv',complete),('selected_full',incomplete)]}
        pairs=driver.pair_reports(results,{'contrasts':{'fixed_pair':{'model_a':'selected_full','model_b':'pv'}}},
            {'selection':{'alternatives':{'fixed_'+str(i):{'mean_ic':0} for i in range(1,5)}}})
        row=[r for r in pairs['fixed_pair']['rows'] if r['window']=='whole_observed'][0]
        self.assertFalse(row['both_full_fixed_window'])
        self.assertIsNone(row['scenario_difference'])
        self.assertIsNone(row['same_path_net_difference_bounds'])
        self.assertFalse(row['point_net_improvement_accepted'])

    def test_actual_checkpoint_byte_hash_guard_with_explicit_nonmodel_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp);config=driver.read_json(driver.HERE/'config.json');models={};hashes={}
            for model in driver.MODELS:
                if model=='selected_full':continue
                folder=run/('fixed/label_'+model[-1] if model.startswith('fixed_label_') else
                    'blo_final/label_1' if model=='blo_selected' else 'ablations/'+model)
                paths=[]
                for seed in config['seeds']:
                    p=folder/('seed_'+str(seed))/'best.pt';p.parent.mkdir(parents=True)
                    p.write_bytes(b'EXPLICIT_STRUCTURAL_HASH_FIXTURE_NOT_MODEL_WEIGHTS')
                    paths.append(str(p));hashes[str(p)]=driver.sha(p)
                models[model]=paths
            models['selected_full']=models['fixed_label_4']
            selection=dict(validation_year=2024,selected_method='fixed_4',selected_label=4,blo_label=1,
                minimum_gain=0.001,alternatives={k:{'mean_ic':0} for k in ['fixed_1','fixed_2','fixed_3','fixed_4','blo']})
            frozen=dict(dataset_fingerprint='EXPLICIT_HASH_CONTRACT_FIXTURE',config=config,selection=selection,
                evaluation_models=models,checkpoint_hashes=hashes,no_test_driven_tuning=True)
            frozen['freeze_id']=hashlib.sha256(json.dumps({'data':frozen['dataset_fingerprint'],'config':config,
                'selection':selection,'hashes':hashes},sort_keys=True).encode()).hexdigest()[:20]
            driver.write_new(run/'freeze.json',frozen)
            driver.verify_freeze(run)
            Path(models['pv'][0]).write_bytes(b'CHANGED_STRUCTURAL_FIXTURE')
            with self.assertRaisesRegex(ValueError,'checkpoint bytes changed'):driver.verify_freeze(run)

    def test_csv_blank_NaN_preserved_and_finite_quality_flag_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'EXPLICIT_CSV_SCHEMA_FIXTURE.csv'
            stamp=pd.Timestamp(datetime(2025,1,2,15,tzinfo=driver.CN)).value
            earliest=pd.Timestamp(datetime(2025,1,3,9,31,tzinfo=driver.CN)).value
            with path.open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(driver.SCORE_COLUMNS)
                for symbol,score,pct in [('600004.SH','', ''),('600005.SH','NaN','NaN'),('600006.SH','0','0.5')]:
                    writer.writerow([symbol,stamp,earliest,score,pct,'CONTRACT_ONLY','feature_missing|label_unmatured'])
            entries=[dict(csv_path=str(path),csv_sha256=driver.sha(path),split='test_2025')]
            frame,coverage=driver.load_daily_scores(entries,'CONTRACT_ONLY',[date(2025,1,2),date(2025,1,3)],
                {date(2025,1,2):{'sh600004','sh600005','sh600006'},date(2025,1,3):set()})
            self.assertTrue(pd.isna(frame.iloc[0].score));self.assertTrue(pd.isna(frame.iloc[1].score))
            self.assertEqual(frame.iloc[2].score,0)
            self.assertEqual(coverage['sections'][str(stamp)]['finite_scores'],1)
            self.assertFalse(coverage['source_complete'])

    def test_fixture_dispatch_all36_independent_incomplete_books(self):
        minute=driver.FrozenMinuteTWAPSource()
        calendar=[d for d in minute.calendar if d>=date(2024,12,1)]
        actual_members=driver.historical_members(calendar)
        pools={d.date():frozenset(g.symbol) for d,g in actual_members.groupby('date')}
        class NoForecast:
            calendar_source='ACTUAL_CALENDAR_NO_FORECAST_CONTRACT_TEST'
            def __init__(self):self.calendar=calendar
            def market_context(self,d,symbols,phase):
                if symbols:raise ValueError('contract has no invented prices')
                return MarketContext(d,{},pools[d],pools[d],'NO_HOLDINGS_NO_PRICE','ACTUAL_DATED_MEMBERS',{})
            def record_actions(self,book,d):return []
            def planning_snapshot(self,*args):raise ValueError('SOURCE_SCORE_INPUT_NOT_SUPPLIED_CONTRACT_TEST')
        providers=[]
        def bind(*args,**kwargs):
            provider=NoForecast();providers.append(provider);return provider
        proto=dict(initial_capital='1000000',contrasts={},trading_source_directory=str(ROOT/'gru_research/trading_data/full_2025_2026'),
            official_benchmark_csv=str(ROOT/'gru_research/trading_data/official_benchmark_2025_2026/normalized/official_csi500_price_2025_2026.csv'))
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);dummy={}
            for filename in ['p.json','s.json','r.json']:
                path=temp/filename;path.write_text('{}');dummy[filename]=path
            manifest=dict(run_directory=str(temp),comparison_protocol_sha256=driver.sha(dummy['p.json']),
                source_seal_sha256=driver.sha(dummy['s.json']),driver_receipt_sha256=driver.sha(dummy['r.json']),
                models=list(driver.MODELS),freeze_sha256='NO_CHECKPOINT_FIXTURE',freeze_id='NO_ACTUAL_SCORES_CONTRACT_ONLY',
                model_versions={m:'NO_ACTUAL_SCORES_CONTRACT_ONLY:'+m for m in driver.MODELS},
                frozen_files={},score_exports={m:[] for m in driver.MODELS})
            driver.write_new(temp/'manifest.json',manifest)
            frozen=dict(freeze_id=manifest['freeze_id'],selection=dict(alternatives={'fixed_'+str(i):{'mean_ic':0} for i in range(1,5)}))
            with patch.object(driver,'PROTOCOL',dummy['p.json']),patch.object(driver,'SEAL',dummy['s.json']),patch.object(driver,'RECEIPT',dummy['r.json']),\
                 patch.object(driver,'verify_source_seal',return_value=(proto,dict(minute_raw_day_files={},immutable_files={}),dict(driver_source_hashes={}))),\
                 patch.object(driver,'verify_freeze',return_value=(frozen,{})),patch.object(driver,'prepare_manifest',return_value=manifest),\
                 patch.object(driver,'load_base_provider',return_value=NoForecast()),patch.object(driver,'bind_frozen_scores',side_effect=bind),\
                 patch.object(driver,'load_daily_scores',return_value=(pd.DataFrame(),dict(source_complete=False))):
                result=driver.run_manifest(temp/'manifest.json',temp/'output')
            self.assertEqual(len(providers),36);self.assertEqual(len({id(p) for p in providers}),36)
            for model in driver.MODELS:
                for scenario in ['primary','stress']:
                    for basis in ['pretax','tax_upper_reserve']:
                        summary=result['results'][model][scenario][basis]
                        self.assertEqual(summary['replay_status'],'INCOMPLETE')
                        self.assertEqual(summary['prefix_diagnostic']['completed_marks'],1)
                        self.assertIsNone(summary['windows']['whole_observed']['scenario_values'])
            self.assertFalse(result['economic_target_accepted'])


if __name__=='__main__':unittest.main()
