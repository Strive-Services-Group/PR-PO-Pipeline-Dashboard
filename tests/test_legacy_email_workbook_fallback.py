import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_legacy_email_workbooks as generator


def legacy_row(**overrides):
    row = {column: None for column in generator.PR_COLUMNS}
    row.update({
        "Purchase requisition": "PR-TEST-001",
        "Status": "In review",
        "Step name": "Unit prices updated in PR lines",
        "Pending Approver/User": "procurement.user",
        "Step date and time": "2026-09-04T06:00:00Z",
        "Total amount": 105.0,
    })
    row.update(overrides)
    return row


def legacy_po_row(**overrides):
    row = {column: None for column in generator.PO_COLUMNS}
    row.update({
        "Purchase order": "PO-TEST-001",
        "Vendor account": "VEND-001",
        "Approval status": "In review",
        "Purchase order status": "Backorder",
        "Step name": "Accounting Manager",
        "Pending Approver/User": "finance.user",
        "Step date and time": "2026-09-04T06:00:00Z",
        "Total amount": 105.0,
    })
    row.update(overrides)
    return row


class LegacyEmailFallbackTests(unittest.TestCase):
    def test_default_main_path_never_reads_the_frozen_snapshot(self):
        dataset = {
            "revision": "test-live-revision", "sourceState": "LIVE",
            "pr": {"rows": [legacy_row(**{"Step name": "Sourcing"})]},
            "po": {"rows": [legacy_po_row(**{"Live stage": "Not yet sent", "Open pipeline": True})]},
        }
        with tempfile.TemporaryDirectory() as output_dir, patch.object(
            generator, "fetch_dataset", return_value=dataset
        ), patch.object(
            generator, "load_legacy_rows", side_effect=AssertionError("snapshot read")
        ), patch.object(sys, "argv", ["generate", "--output-dir", output_dir]):
            generator.main()

    def test_live_holder_rejects_comma_joined_data_fault(self):
        source = legacy_row(**{
            "Step name": "Sourcing",
            "Pending Approver/User": "Adnan.Ullah, adnan.ullah, Layusha.cleatus",
        })
        with self.assertRaisesRegex(RuntimeError, "more than one owner"):
            generator.live_pr_rows([source])

    def test_live_single_holder_stays_single(self):
        rows, _ = generator.live_pr_rows([legacy_row(**{
            "Step name": "Sourcing", "Pending Approver/User": "Aparna.Pauly"
        })])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Pending Approver/User"], "Aparna.Pauly")

    def test_export_total_wins_for_vat_and_zero_rated_rows(self):
        vat, zero = generator.live_pr_rows([
            legacy_row(**{"Purchase requisition": "PR-VAT", "Total amount": 105.0, "Line total": 100.0}),
            legacy_row(**{"Purchase requisition": "PR-ZERO", "Total amount": 100.0, "Line total": 100.0}),
        ])[0]
        self.assertEqual(vat["Total amount"], 105.0)
        self.assertEqual(zero["Total amount"], 100.0)

    def test_po_export_owner_and_step_are_not_dropped(self):
        rows, _ = generator.live_po_rows([legacy_po_row(**{
            "Pending Approver/User": "arman.b", "Step name": "Accounting Manager"
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "arman.b")
        self.assertEqual(rows[0]["Step name"], "Accounting Manager")

    def test_live_blank_holder_is_explicit(self):
        rows, _ = generator.live_pr_rows([legacy_row(**{
            "Step name": "Sourcing", "Pending Approver/User": ""
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "No named owner — Pending Approver/User not recorded in F&O export; preparer: not recorded")

    def test_live_missing_step_remains_blank(self):
        rows, evidence = generator.live_pr_rows([legacy_row(**{
            "Purchase requisition": "PR-NEW-AFTER-SNAPSHOT",
            "Step name": None,
            "Stage reason code": "UNMAPPED_ELEMENT",
            "Pending Approver/User": "roderick.red",
        })])
        self.assertEqual(rows[0]["Purchase requisition"], "PR-NEW-AFTER-SNAPSHOT")
        self.assertEqual(rows[0]["Step name"], "")
        self.assertEqual(evidence["step not reported source documents"], 1)

    def test_priced_work_stays_with_export_owner_and_step(self):
        rows, evidence = generator.live_pr_rows([legacy_row(**{
            "Step name": "Priced — awaiting approval",
            "Stage reason code": "ACTIVE_LINES_PRICED",
            "Department": "Building Services",
            "Pending Approver/User": "Adnan.Ullah",
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "Adnan.Ullah")
        self.assertEqual(rows[0]["Step name"], "Priced — awaiting approval")
        self.assertEqual(evidence["operations confirmation source documents"], 0)

    def test_priced_without_department_mapping_still_keeps_export_owner(self):
        rows, evidence = generator.live_pr_rows([legacy_row(**{
            "Step name": "Priced — awaiting approval",
            "Stage reason code": "ACTIVE_LINES_PRICED",
            "Department": "Surveying Services",
            "Pending Approver/User": "Aparna.Pauly",
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "Aparna.Pauly")
        self.assertEqual(evidence["no named owner source documents"], 0)

    def test_every_stage_reason_code_has_a_plain_english_class(self):
        for code, rule in generator.WORK_CLASS_RULE["classes"].items():
            with self.subTest(code=code):
                _, resolved = generator.work_class(legacy_row(**{"Stage reason code": code}))
                self.assertEqual(resolved["label"], rule["label"])
                self.assertTrue(resolved["action"])

    def test_preparer_is_information_and_is_not_promoted(self):
        rows, _ = generator.live_pr_rows([legacy_row(**{
            "Step name": None,
            "Stage reason code": "NO_CURRENT_WORK_ITEM",
            "Preparer": "310523",
            "Pending Approver/User": None,
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "No named owner — Pending Approver/User not recorded in F&O export; preparer: 310523")
        self.assertEqual(rows[0]["Preparer"], "310523")

    def test_unknown_preparer_employee_number_remains_information(self):
        rows, evidence = generator.live_pr_rows([legacy_row(**{
            "Step name": None,
            "Stage reason code": "NO_CURRENT_WORK_ITEM",
            "Preparer": "999999",
            "Pending Approver/User": None,
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "No named owner — Pending Approver/User not recorded in F&O export; preparer: 999999")
        self.assertEqual(evidence["no named owner source documents"], 1)

    def test_system_account_preparer_is_not_promoted(self):
        rows, evidence = generator.live_pr_rows([legacy_row(**{
            "Step name": None,
            "Stage reason code": "NO_CURRENT_WORK_ITEM",
            "Preparer": "000000",
            "Pending Approver/User": None,
        })])
        self.assertEqual(rows[0]["Pending Approver/User"], "No named owner — Pending Approver/User not recorded in F&O export; preparer: 000000")
        self.assertEqual(evidence["no named owner source documents"], 1)

    def test_delivery_classifies_inactive_unaddressed_and_addressed_holders(self):
        self.assertEqual(
            generator.delivery_classification("Layusha.cleatus"),
            ("no named owner", "no active owner"),
        )
        self.assertEqual(
            generator.delivery_classification("Sirinikhil"),
            ("no named owner", "no email address on file"),
        )
        self.assertEqual(
            generator.delivery_classification("Zaheer Ahmed Ameer"),
            ("no named owner", "no email address on file"),
        )

    def test_delivery_classification_covers_every_attribution_once(self):
        owners = ["Layusha.cleatus", "Sirinikhil", "Zaheer.Ahmed", "No named owner — IT DEPARTMENT"]
        routes = [generator.delivery_classification(owner)[0] for owner in owners]
        self.assertEqual(routes.count("named personal email"), 1)
        self.assertEqual(routes.count("no named owner"), 3)
        self.assertEqual(len(routes), 4)

    def test_preserves_routing_and_replaces_amount_from_live_source(self):
        live = [{"Purchase requisition": "pr-test-001", "Total amount": 100.0}]
        rows, evidence = generator.fallback_pr_rows(live, [legacy_row()])
        self.assertEqual(rows[0]["Step name"], "Unit prices updated in PR lines")
        self.assertEqual(rows[0]["Pending Approver/User"], "procurement.user")
        self.assertEqual(rows[0]["Step date and time"], "2026-09-04T06:00:00Z")
        self.assertEqual(rows[0]["Total amount"], 100.0)
        self.assertEqual(evidence["live ex-VAT amount joined"], 1)

    def test_fails_if_an_actionable_snapshot_row_has_no_live_amount(self):
        with self.assertRaisesRegex(RuntimeError, "actionable fallback requisition"):
            generator.fallback_pr_rows([], [legacy_row()])

    def test_omits_only_a_non_actionable_row_missing_from_live_source(self):
        row = legacy_row(Status="Closed")
        rows, evidence = generator.fallback_pr_rows([], [row])
        self.assertEqual(rows, [])
        self.assertEqual(evidence["unavailable non-action row omitted"], 1)

    def test_rejects_a_comma_joined_owner(self):
        live = [{"Purchase requisition": "PR-TEST-001", "Total amount": 100.0}]
        row = legacy_row(**{"Pending Approver/User": "one.user, two.user"})
        with self.assertRaisesRegex(RuntimeError, "multiple owners"):
            generator.fallback_pr_rows(live, [row])

    def test_generated_workbook_keeps_exact_header_contract_without_routing_metadata(self):
        payload = generator.workbook_bytes(
            [legacy_row()], generator.PR_COLUMNS, generator.PR_WIDTHS,
            generator.PR_DATE_COLUMNS
        )
        workbook = load_workbook(io.BytesIO(payload), read_only=False, data_only=True)
        sheet = workbook.active
        headers = [sheet.cell(1, index).value for index in range(1, sheet.max_column + 1)]
        self.assertEqual(headers, generator.PR_COLUMNS)
        self.assertEqual(headers[-1], "Stage reason code")
        self.assertEqual(list(sheet.tables), ["AxTable1"])
        self.assertNotIn("Routing metadata", workbook.sheetnames)

    def test_po_fallback_preserves_routing_and_replaces_live_amount(self):
        live = [{"Purchase order": "po-test-001", "Vendor account": "vend-001", "Total amount": 100.0}]
        rows, evidence = generator.fallback_po_rows(live, [legacy_po_row()])
        self.assertEqual(rows[0]["Step name"], "Accounting Manager")
        self.assertEqual(rows[0]["Pending Approver/User"], "finance.user")
        self.assertEqual(rows[0]["Total amount"], 100.0)
        self.assertEqual(evidence["live ex-VAT amount joined"], 1)

    def test_po_fallback_fails_without_a_live_amount(self):
        with self.assertRaisesRegex(RuntimeError, "purchase order missing"):
            generator.fallback_po_rows([], [legacy_po_row()])

    def test_po_amount_join_disambiguates_reused_numbers_by_vendor(self):
        live = [
            {"Purchase order": "PO-TEST-001", "Vendor account": "VEND-001", "Total amount": 100.0},
            {"Purchase order": "PO-TEST-001", "Vendor account": "VEND-002", "Total amount": 900.0},
        ]
        rows, _ = generator.fallback_po_rows(live, [legacy_po_row()])
        self.assertEqual(rows[0]["Total amount"], 100.0)


if __name__ == "__main__":
    unittest.main()
