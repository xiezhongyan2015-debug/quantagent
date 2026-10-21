"""Prepared-only synthetic field/AST checks; no actual replay/provider calls."""
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import ast
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from target_decimal_quote_overlay_v1 import NAMESPACE, patch_window_fields
from target_decimal_research_replay_v1 import namespace_ast


@dataclass(frozen=True)
class Quote:
    symbol: str
    raw_price: Decimal
    complete: bool = True
    observed_positive_volume: bool = True
    all_five_minutes_flat: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class Window:
    signal_time: int
    execution_start: int
    execution_end: int
    quotes: dict
    source: str
    price_kind: str = 'ORIGINAL_SYNTHETIC_FIXTURE'


def fixture():
    start = 1739151060000000000
    root = '/synthetic/old_hourly.parquet'
    overlay = dict(overlay_id='synthetic-only', source_pins=dict(old_hourly=dict(path=root),
        payload=dict(path='/synthetic/source.parquet',sha256='a'*64),
        four_window_evidence=dict(path='/synthetic/evidence.json',sha256='b'*64)),
        quotes=[dict(start_UTC_ns=start,end_UTC_ns=start+300000000000,
            original_double_as_decimal='7.610000000000001',exact_decimal_price='7.61',
            original_row_ordinals=[1,2,3,4,5],individual_open_decimals=['7.61']*5,
            individual_open_binary_hex=['0x1.e70a3d70a3d71p+2']*5,volume_shares=535609)])
    window = Window(1,start,start+300000000000,
        {'sh600126':Quote('sh600126',Decimal('7.610000000000001')),
         'sh600004':Quote('sh600004',Decimal('7.64'))},root)
    return window,overlay


class TargetDecimalContract(unittest.TestCase):
    def test_target_only_price_changes_with_explicit_source_provenance(self):
        window,overlay=fixture();updated,event=patch_window_fields(window,overlay)
        self.assertEqual(updated.quotes['sh600126'],replace(window.quotes['sh600126'],raw_price=Decimal('7.61')))
        self.assertIs(updated.quotes['sh600004'],window.quotes['sh600004'])
        self.assertEqual(updated.source,window.source)
        self.assertIn(NAMESPACE,updated.price_kind)
        self.assertEqual(event['old_quote_raw_price'],'7.610000000000001')
        self.assertEqual(event['new_quote_raw_price'],'7.61')
        self.assertTrue(event['old_hourly_path_SHA_is_inherited_file_reference_not_new_quote_numeric_source'])
        import numpy as np
        native=replace(window.quotes['sh600126'],complete=np.bool_(True),observed_positive_volume=np.bool_(True))
        native_window=replace(window,quotes=dict(window.quotes,sh600126=native))
        native_updated,_=patch_window_fields(native_window,overlay)
        self.assertIs(native_updated.quotes['sh600126'].complete,native.complete)
        self.assertIs(native_updated.quotes['sh600126'].observed_positive_volume,native.observed_positive_volume)

    def test_other_symbol_and_other_window_return_original_object(self):
        window,overlay=fixture()
        for other in (replace(window,quotes={'sh600004':window.quotes['sh600004']}),
                      replace(window,execution_start=window.execution_start+60000000000)):
            updated,event=patch_window_fields(other,overlay)
            self.assertIs(updated,other);self.assertIsNone(event)

    def test_matching_target_eligibility_and_old_identity_cannot_be_repaired(self):
        window,overlay=fixture()
        for invalid in (replace(window,source='/synthetic/other.parquet'),
                        replace(window,execution_end=window.execution_end+60000000000),
                        replace(window,quotes={'sh600126':replace(window.quotes['sh600126'],complete=False)}),
                        replace(window,quotes={'sh600126':replace(window.quotes['sh600126'],complete='True')}),
                        replace(window,quotes={'sh600126':replace(window.quotes['sh600126'],observed_positive_volume=1)}),
                        replace(window,quotes={'sh600126':replace(window.quotes['sh600126'],raw_price=Decimal('7.62'))})):
            with self.assertRaises(ValueError):patch_window_fields(invalid,overlay)

    def test_original_run_AST_reverse_namespace_change_is_exact(self):
        path=Path(__file__).resolve().parents[1]/'trading_comparison_v4.py'
        adapted,original_dump=namespace_ast(path.read_text())
        assignments=[node for node in ast.walk(adapted) if isinstance(node,ast.Assign)
            and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='run_id']
        self.assertEqual(len(assignments),1)
        node=assignments[0];self.assertIsInstance(node.value,ast.BinOp)
        self.assertEqual(node.value.left.value,NAMESPACE+':')
        node.value=node.value.right
        self.assertEqual(ast.dump(adapted,include_attributes=False),original_dump)


if __name__=='__main__':unittest.main()
